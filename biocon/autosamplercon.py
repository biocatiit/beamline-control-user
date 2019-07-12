# coding: utf-8
#
#    Project: BioCAT user beamline control software (BioCON)
#             https://github.com/biocatiit/beamline-control-user
#
#
#    Principal author:       Jesse Hopkins
#
#    This is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This software is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this software.  If not, see <http://www.gnu.org/licenses/>.
from __future__ import absolute_import, division, print_function, unicode_literals
from builtins import object, range, map
from io import open

import threading
import time
from collections import deque, OrderedDict
import logging
import sys
import copy
import platform

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import numpy as np
import wx
import zaber.serial as zaber
from six import string_types

import motorcon
import valvecon

class WellPlate(object):

    def __init__(self, plate_type):

        self.plate_params = {
            'Abgene 96 well deepwell storage' : {
                'max_volume'    : 200, # uL
                'num_columns'   : 12,
                'num_rows'      : 8,
                'col_step'      : 9.00, # mm
                'row_step'      : 9.00, # mm
                'height'        : 4.2, # bottom of well above top of chiller base plate
                },

            'Thermo-Fast 96 well PCR' : {
                'max_volume'    : 200, # uL
                'num_columns'   : 12,
                'num_rows'      : 8,
                'col_step'      : 9.00, # mm
                'row_step'      : 9.00, # mm
                'height'        : 0.8, # bottom of well above top of chiller base plate
                }
        }

        #Values for height depend on well. 0.8 for A1, 1.0 for H1, 0.8 for A12, 1.0 for H12
        self.x_slope = 0
        self.y_slope = 0.20/7.

        self.update_plate_type(plate_type)

    def update_plate_type(self, plate_type):

        self.plate_type = plate_type

        self.max_volume = self.plate_params[self.plate_type]['max_volume']
        self.num_columns = self.plate_params[self.plate_type]['num_columns']
        self.num_rows = self.plate_params[self.plate_type]['num_rows']
        self.num_wells = self.num_columns*self.num_rows

        self.well_volumes = np.zeros((self.num_rows, self.num_columns))

        self.col_step = self.plate_params[self.plate_type]['col_step']
        self.row_step = self.plate_params[self.plate_type]['row_step']
        self.height = self.plate_params[self.plate_type]['height']

    def get_relative_well_position(self, row, column):
        """
        Expects column and row to be 1 indexed, like on a plate!
        """
        if isinstance(row, string_types):
            row = ord(row.lower()) - 96

        if row > self.num_rows or column > self.num_columns or row < 1 or column < 1:
            raise ValueError('Invalid row or column')

        column = int(column)-1
        row = int(row)-1

        return np.array([column*(self.col_step), row*(self.row_step),
            -(self.height+column*self.x_slope+row*self.y_slope)], dtype=np.float_)

    def set_well_volume(self, volume, row, column):
        """
        Expects column and row to be 1 indexed, like on a plate!
        """
        if isinstance(row, string_types):
            row = ord(row.lower()) - 96

        column = int(column)-1
        row = int(row)-1

        self.well_volumes[row, column] = float(volume)

    def get_well_volume(self, row, column):
        """
        Expects column and row to be 1 indexed, like on a plate!
        """
        if isinstance(row, string_types):
            row = ord(row.lower()) - 96

        column = int(column)-1
        row = int(row)-1

        return self.well_volumes[row, column]

    def set_all_well_volumes(self, volume):

        self.well_volumes[:,:] = float(volume)

    def get_all_well_volumes(self):
        return self.well_volumes

    def get_known_plates(self):
        return self.plate_params.keys()


class Autosampler(object):

    def __init__(self, settings):

        self.settings = settings

        self.abort_event = threading.Event()
        self.abort_event.clear()
        self.running_event = threading.Event()
        self.running_event.clear()

        self._init_motors()
        self._init_valves()

        self.set_well_plate(self.settings['plate_type'])
        self.set_chiller_top_on(self.settings['chiller_top_on'])
        self.set_clean_sequence(self.settings['clean_buffer_seq'], 'buffer')
        self.set_clean_sequence(self.settings['clean_sample_seq'], 'sample')

    def _init_motors(self):
        logger.info('Initializing autosampler motors')
        self.motor_lock = threading.Lock()

        if self.settings['motors'] == 'zaber':
            self.zaber_ports = {}

            for motor_name, motor_settings in self.settings['zaber_motors'].items():
                port = motor_settings[0]
                number = motor_settings[1]
                travel = motor_settings[2]

                if port not in self.zaber_ports:
                    binary_serial = zaber.BinarySerial(str(port))
                    binary_serial.close()
                    lock = threading.Lock()
                    self.zaber_ports[port] = (binary_serial, lock)

                    binary_serial.lock.acquire()

                    try:
                        binary_serial.open()
                        while binary_serial.can_read():
                            reply = binary_serial.read()
                        # Device number 0, command number 2, renumber.
                        command = zaber.BinaryCommand(0, 2)
                        binary_serial.write(command)

                        time.sleep(5)

                        while binary_serial.can_read():
                            reply = binary_serial.read()
                            if motorcon.ZaberMotor.check_command_succeeded(reply):
                                logger.debug("Zaber device renumbered")
                            else:
                                logger.error("Zaber device renumbering failed")

                    except Exception:
                        raise
                    finally:
                        binary_serial.close()

                    binary_serial.lock.release()

                else:
                    binary_serial, lock = self.zaber_ports[port]

                if motor_name == 'x':
                    self.motor_x = motorcon.ZaberMotor(port, motor_name,
                        binary_serial, lock, number, travel)

                elif motor_name == 'y':
                    self.motor_y = motorcon.ZaberMotor(port, motor_name,
                        binary_serial, lock, number, travel)

                elif motor_name == 'z':
                    self.motor_z = motorcon.ZaberMotor(port, motor_name,
                        binary_serial, lock, number, travel)

        velocities = [self.settings['motor_velocity']['x'],
            self.settings['motor_velocity']['y'],
            self.settings['motor_velocity']['z']]

        self.set_motor_velocity(velocities)

        accels = [self.settings['motor_acceleration']['x'],
            self.settings['motor_acceleration']['y'],
            self.settings['motor_acceleration']['z']]

        self.set_motor_acceleration(accels)

        self.set_base_position(self.settings['base_position']['x'],
            self.settings['base_position']['y'], self.settings['base_position']['z'])

        self.set_clean_position(self.settings['clean_position']['x'],
            self.settings['clean_position']['y'], self.settings['clean_position']['z'])

        self.set_out_position(self.settings['out_position']['x'],
            self.settings['out_position']['y'], self.settings['out_position']['z'])

    def _init_valves(self):
        logger.info('Initializing autosampler valves')
        if self.settings['valves'] == 'rheodyne':
            self.injection_valve = valvecon.RheodyneValve(
                self.settings['rheodyne_valves']['injection'][0], 'injection',
                self.settings['rheodyne_valves']['injection'][1])

            self.sample_valve = valvecon.RheodyneValve(
                self.settings['rheodyne_valves']['sample'][0], 'sample',
                self.settings['rheodyne_valves']['sample'][1])

            self.buffer_valve = valvecon.RheodyneValve(
                self.settings['rheodyne_valves']['buffer'][0], 'buffer',
                self.settings['rheodyne_valves']['buffer'][1])

            self.bypass_valve = valvecon.RheodyneValve(
                self.settings['rheodyne_valves']['bypass'][0], 'bypass',
                self.settings['rheodyne_valves']['bypass'][1])

            self.autosampler_valve = valvecon.RheodyneValve(
                self.settings['rheodyne_valves']['autosampler'][0], 'autosampler',
                self.settings['rheodyne_valves']['autosampler'][1])

        valve_pos = [self.settings['valve_positions']['injection'],
            self.settings['valve_positions']['sample'], 
            self.settings['valve_positions']['buffer'],
            self.settings['valve_positions']['bypass'],
            self.settings['valve_positions']['autosampler']]

        self.set_valve_positions(valve_pos)


    def home_motors(self, motor='all'):
        self.running_event.set()
        if motor == 'all':
            old_velocities = [self.x_velocity, self.y_velocity, self.z_velocity]
            home_velocities = [self.settings['motor_home_velocity']['x'],
                self.settings['motor_home_velocity']['y'],
                self.settings['motor_home_velocity']['z']]
            self.set_motor_velocity(home_velocities)
            self.motor_z.home(False)
            time.sleep(0.05)
            self.motor_x.home(False)
            time.sleep(0.05)    # Necessary for the Zabers for some reason
            self.motor_y.home(False)
            time.sleep(0.05)

            while self.motor_x.is_moving() or self.motor_y.is_moving() or self.motor_z.is_moving():
                time.sleep(0.05)
                abort = self._check_abort()
                if abort:
                    break

            self.set_motor_velocity(old_velocities)

        elif motor == 'x':
            logger.info('Homing x motor')
            old_velocity = self.x_velocity
            self.set_motor_velocity(self.settings['motor_home_velocity']['x'], 'x')
            self.motor_x.home(False)

            while self.motor_x.is_moving():
                time.sleep(0.01)
                abort = self._check_abort()
                if abort:
                    break

            self.set_motor_velocity(old_velocity, 'x')

        elif motor == 'y':
            old_velocity = self.y_velocity
            self.set_motor_velocity(self.settings['motor_home_velocity']['y'], 'y')
            self.motor_y.home(False)

            while self.motor_y.is_moving():
                time.sleep(0.01)
                abort = self._check_abort()
                if abort:
                    break

            self.set_motor_velocity(old_velocity, 'y')

        elif motor == 'z':
            old_velocity = self.z_velocity
            self.set_motor_velocity(self.settings['motor_home_velocity']['z'], 'z')
            self.motor_z.home(False)

            while self.motor_z.is_moving():
                time.sleep(0.01)
                abort = self._check_abort()
                if abort:
                    break

            self.set_motor_velocity(old_velocity, 'z')

        self.running_event.clear()

    def move_motors_absolute(self, position, motor='all'):
        self.running_event.set()

        if motor == 'all':
            self.motor_z.move_absolute(position[2], blocking=False)
            time.sleep(0.05)
            self.motor_x.move_absolute(position[0], blocking=False)
            time.sleep(0.05)
            self.motor_y.move_absolute(position[1], blocking=False)
            time.sleep(0.05)

            while self.motor_x.is_moving() or self.motor_y.is_moving() or self.motor_z.is_moving():
                time.sleep(0.05)
                abort = self._check_abort()
                if abort:
                    break

        elif motor == 'x':
            self.motor_x.move_absolute(position, False)
            time.sleep(0.05)

            while self.motor_x.is_moving():
                time.sleep(0.01)
                abort = self._check_abort()
                if abort:
                    break

        elif motor == 'y':
            self.motor_y.move_absolute(position, False)
            time.sleep(0.05)

            while self.motor_y.is_moving():
                time.sleep(0.01)
                abort = self._check_abort()
                if abort:
                    break

        elif motor == 'z':
            self.motor_z.move_absolute(position, False)
            time.sleep(0.05)

            while self.motor_z.is_moving():
                time.sleep(0.01)
                abort = self._check_abort()
                if abort:
                    break

        self.running_event.clear()

    def move_motors_relative(self, position, motor='all'):
        self.running_event.set()

        if motor == 'all':
            self.motor_z.move_relative(position[2], blocking=False)
            time.sleep(0.05)
            self.motor_x.move_relative(position[0], blocking=False)
            time.sleep(0.05)
            self.motor_y.move_relative(position[1], blocking=False)
            time.sleep(0.05)

            while self.motor_x.is_moving() or self.motor_y.is_moving() or self.motor_z.is_moving():
                time.sleep(0.05)
                abort = self._check_abort()
                if abort:
                    break

        elif motor == 'x':
            self.motor_x.move_relative(position, False)
            time.sleep(0.05)

            while self.motor_x.is_moving():
                time.sleep(0.01)
                abort = self._check_abort()
                if abort:
                    break

        elif motor == 'y':
            self.motor_y.move_relative(position, False)
            time.sleep(0.05)

            while self.motor_y.is_moving():
                time.sleep(0.01)
                abort = self._check_abort()
                if abort:
                    break

        elif motor == 'z':
            self.motor_z.move_relative(position, False)
            time.sleep(0.05)

            while self.motor_z.is_moving():
                time.sleep(0.01)
                abort = self._check_abort()
                if abort:
                    break

        self.running_event.clear()

    def set_motor_velocity(self, velocity, motor='all'):
        if motor == 'all':
            self.x_velocity = float(velocity[0])
            self.y_velocity = float(velocity[1])
            self.z_velocity = float(velocity[2])

            self.motor_x.set_velocity(self.x_velocity)
            self.motor_y.set_velocity(self.y_velocity)
            self.motor_z.set_velocity(self.z_velocity)

        elif motor == 'x':
            self.x_velocity = float(velocity)
            self.motor_x.set_velocity(self.x_velocity)

        elif motor == 'y':
            self.y_velocity = float(velocity)
            self.motor_y.set_velocity(self.y_velocity)

        elif motor == 'z':
            self.z_velocity = float(velocity)
            self.motor_z.set_velocity(self.z_velocity)


    def set_motor_acceleration(self, accel, motor='all'):
        if motor == 'all':
            self.x_accel = float(accel[0])
            self.y_accel = float(accel[1])
            self.z_accel = float(accel[2])

            self.motor_x.set_acceleration(self.x_accel)
            self.motor_y.set_acceleration(self.y_accel)
            self.motor_z.set_acceleration(self.z_accel)

        elif motor == 'x':
            self.x_accel = float(accel)
            self.motor_x.set_acceleration(self.x_accel)

        elif motor == 'y':
            self.y_accel = float(accel)
            self.motor_y.set_acceleration(self.y_accel)

        elif motor == 'z':
            self.z_accel = float(accel)
            self.motor_z.set_acceleration(self.z_accel)

    def _check_abort(self):
        if self.abort_event.is_set():
            self.motor_x.stop()
            self.motor_y.stop()
            self.motor_z.stop()

            self.set_valve_positions(1, 'injection')
            self.set_valve_positions(2, 'bypass')
            self.set_valve_position(2, 'sample')
            self.set_valve_position(1, 'buffer')

            self.abort_event.clear()

            abort = True

        else:
            abort = False

        return abort

    def stop(self):
        if self.running_event.is_set():
            self.abort_event.set()

    def set_base_position(self, x, y, z):
        """
        This sets the base position from which relative well positions are
        calculated based on the definitions in the WellPlate class.

        This should be the position with the needle centered in the A1 well
        and tip height at the top of the lower chiller plate.
        """

        self.base_position = np.array([x, y, z], dtype=np.float_)

    def set_clean_position(self, x, y, z):
        self.clean_position = np.array([x, y, z], dtype=np.float_)

    def set_out_position(self, x, y, z):
        self.out_position = np.array([x, y, z], dtype=np.float_)

    def set_well_plate(self, plate_type):
        self.well_plate = WellPlate(plate_type)

    def set_well_volume(self, volume, row, column):
        """
        Expects column and row to be 1 indexed, like on a plate!
        """
        self.well_plate.set_well_volume(volume, row, column)

    def get_well_volume(self, row, column):
        """
        Expects column and row to be 1 indexed, like on a plate!
        """
        volume = self.well_plate.get_well_volume(row, column)

        return volume

    def set_all_well_volumes(self, volume):

        self.well_plate.set_all_well_volumes(volume)

    def get_all_well_volumes(self):
        well_volumes = self.well_plate.get_all_well_volumes()

        return well_volumes

    def set_chiller_top_on(self, status):
        self.chiller_top_on = status

    def move_to_load(self, row, column):
        if self.chiller_top_on:
            if isinstance(row, string_types):
                row = ord(row.lower()) - 96

            if row%2 != 0:
                raise ValueError('Cannot access odd rows with chiller top plate on!')
        delta_position = self.well_plate.get_relative_well_position(row, column)

        well_position = self.base_position + delta_position

        self.move_motors_absolute(self.out_position[2], 'z')
        self.move_motors_absolute([well_position[0], well_position[1],
            self.out_position[2]])
        self.move_motors_absolute(well_position[2], 'z')

    def move_to_clean(self):
        self.move_motors_absolute(self.out_position[2], 'z')
        self.move_motors_absolute([self.clean_position[0], self.clean_position[1],
            self.out_position[2]])
        self.move_motors_absolute(self.clean_position[2], 'z')

    def move_to_out(self):
        self.move_motors_absolute(self.out_position[2], 'z')
        self.move_motors_absolute(self.out_position)

    def set_valve_positions(self, positions, valve='all'):
        self.running_event.set()

        if valve == 'all':
            self.injectionv_position = int(positions[0])
            self.samplev_position = int(positions[1])
            self.bufferv_position = int(positions[2])
            self.bypassv_position = int(positions[3])
            self.autosamplerv_position = int(positions[4])

            self.injection_valve.set_position(self.injectionv_position)
            self.sample_valve.set_position(self.samplev_position)
            self.buffer_valve.set_position(self.bufferv_position)
            self.bypass_valve.set_position(self.bypassv_position)
            self.autosampler_valve.set_position(self.autosamplerv_position)

        elif valve == 'injection':
            self.injectionv_position = int(positions)
            self.injection_valve.set_position(self.injectionv_position)

        elif valve == 'sample':
            self.samplev_position = int(positions)
            self.sample_valve.set_position(self.samplev_position)

        elif valve == 'buffer':
            self.bufferv_position = int(positions)
            self.buffer_valve.set_position(self.bufferv_position)

        elif valve == 'bypass':
            self.bypassv_position = int(positions)
            self.bypass_valve.set_position(self.bypassv_position)

        elif valve == 'autosampler':
            self.autosamplerv_position = int(positions)
            self.autosampler_valve.set_position(self.autosamplerv_position)

        self.running_event.clear()

    def load_buffer(self, volume, row, column):
        self.move_to_load(row, column)

        self.set_valve_positions(1, 'bypass')
        self.set_valve_positions(2, 'buffer')
        self.set_valve_positions(5, 'autosampler')

        #Aspirate buffer

        self.set_valve_positions(1, 'injection')
        self.set_valve_positions(1, 'buffer')

        #Push enough buffer to flush coflow needle (20 uL or ~3x volume from buffer valve to needle)

        #Clean lines running to autosampler (by cleaning sample loop)?

    def load_sample(self, volume, row, column):
        self.move_to_load(row, column)

        self.set_valve_positions(1, 'injection')
        self.set_valve_positions(1, 'sample')
        self.set_valve_positions(4, 'autosampler')

        #Aspirate sample

    def make_measurement(self, row, column):
        self.set_valve_positions(1, 'injection')
        self.set_valve_positions(1, 'sample')
        self.set_valve_positions(1, 'buffer')
        self.set_valve_positions(1, 'bypass')

        # Start buffer flow and exposure

        # After ~10 uL, of buffer flow
        self.set_valve_positions(2, 'injection')

        #Continue flow until almost out of buffer, then stop before running out

        #Flow rates ideally 100-200 uL/min?

    def set_clean_sequence(self, seq, loop):
        if loop == 'sample':
            self.clean_sample_seq = seq
        elif loop == 'buffer':
            self.clean_buffer_seq = seq

    def clean_sample(self):
        self.move_to_clean()
        self.set_valve_positions(1, 'injection')
        self.set_valve_positions(2, 'bypass')
        self.set_valve_positions(4, 'autosampler')

        for clean_step in self.clean_sample_seq:
            self.set_valve_positions(clean_step[0], 'sample')
            self.running_event.set()
            self._sleep(clean_step[1])
            self.running_event.clear()

        self.set_valve_positions(2, 'sample')
        self.move_to_out()

    def clean_buffer(self):
        self.set_valve_positions(2, 'bypass')

        for clean_step in self.clean_buffer_seq:
            self.set_valve_positions(clean_step[0], 'buffer')
            self.running_event.set()
            self._sleep(clean_step[1])
            self.running_event.clear()

        self.set_valve_positions(2, 'buffer')

    def _sleep(self, sleep_time):
        start = time.time()

        while time.time() - start < sleep_time:
            time.sleep(0.01)
            abort = self._check_abort()
            if abort:
                break

        return abort



class AutosamplerPanel(wx.Panel):
    """
    This flow meter panel supports standard settings, including connection settings,
    for a flow meter. It is meant to be embedded in a larger application and can
    be instanced several times, once for each flow meter. It communciates
    with the flow meters using the :py:class:`FlowMeterCommThread`. Currently
    it only supports the :py:class:`BFS`, but it should be easy to extend for
    other flow meters. The only things that should have to be changed are
    are adding in flow meter-specific readouts, modeled after how the
    ``bfs_pump_sizer`` is constructed in the :py:func:`_create_layout` function,
    and then add in type switching in the :py:func:`_on_type` function.
    """
    def __init__(self, settings, *args, **kwargs):
        """
        Initializes the custom thread. Important parameters here are the
        list of known commands ``_commands`` and known pumps ``known_fms``.

        :param wx.Window parent: Parent class for the panel.

        :param int panel_id: wx ID for the panel.

        :param str panel_name: Name for the panel

        :param list all_comports: A list containing all comports that the flow meter
            could be connected to.

        :param collections.deque fm_cmd_q: The ``fm_cmd_q`` that was passed to
            the :py:class:`FlowMeterCommThread`.

        :param collections.deque fm_return_q: The ``fm_return_q`` that was passed to
            the :py:class:`FlowMeterCommThread`.

        :param list known_fms: The list of known flow meter types, obtained from
            the :py:class:`FlowMeterCommThread`.

        :param str fm_name: An identifier for the flow meter, displayed in the
            flow meter panel.

        :param str fm_type: One of the ``known_fms``, corresponding to the flow
            meter connected to this panel. Only required if you are connecting
            the flow meter when the panel is first set up (rather than manually
            later).

        :param str comport: The comport the flow meter is connected to. Only required
            if you are connecting the flow meter when the panel is first set up (rather
            than manually later).

        :param list fm_args: Flow meter specific arguments for initialization.
            Only required if you are connecting the flow meter when the panel is first
            set up (rather than manually later).

        :param dict fm_kwargs: Flow meter specific keyword arguments for initialization.
            Only required if you are connecting the flow meter when the panel is first
            set up (rather than manually later).

        """

        super(AutosamplerPanel, self).__init__(*args, **kwargs)
        logger.debug('Initializing CoflowPanel')

        self.settings = settings

        self._create_layout()

    def _create_layout(self):
        pass

    def metadata(self):

       pass

    def on_exit(self):
        pass

class AutosamplerFrame(wx.Frame):
    """
    A lightweight frame allowing one to work with arbitrary number of pumps.
    Only meant to be used when the pumpcon module is run directly,
    rather than when it is imported into another program.
    """
    def __init__(self, settings, *args, **kwargs):
        """
        Initializes the pump frame. Takes args and kwargs for the wx.Frame class.
        """
        super(AutosamplerFrame, self).__init__(*args, **kwargs)
        logger.debug('Setting up the AutosamplerFrame')

        self.Bind(wx.EVT_CLOSE, self._on_exit)

        self._create_layout(settings)

        self.Layout()
        self.SendSizeEvent()
        self.Fit()
        self.Raise()

    def _create_layout(self, settings):
        """Creates the layout"""
        self.autosampler_panel = AutosamplerPanel(settings, self)

        self.autosampler_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.autosampler_sizer.Add(self.autosampler_panel, proportion=1, flag=wx.EXPAND)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(self.autosampler_sizer, proportion=1, flag=wx.EXPAND|wx.ALL, border=5)

        self.SetSizer(top_sizer)

    def _on_exit(self, evt):
        """Stops all current pump motions and then closes the frame."""
        logger.debug('Closing the AutosamplerFrame')

        self.autosampler_panel.on_exit()

        self.Destroy()

if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.INFO)
    # h1.setLevel(logging.DEBUG)
    # h1.setLevel(logging.ERROR)

    # formatter = logging.Formatter('%(asctime)s - %(message)s')
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    # logger = logging.getLogger('biocon')
    # logger.setLevel(logging.DEBUG)
    # h1 = logging.StreamHandler(sys.stdout)
    # h1.setLevel(logging.INFO)
    # formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    # h1.setFormatter(formatter)
    # logger.addHandler(h1)

    #Settings
    settings = {
        'device_communication'  : 'local',
        # 'remote_pump_ip'        : '164.54.204.37',
        # 'remote_pump_port'      : '5556',
        # 'remote_fm_ip'          : '164.54.204.37',
        # 'remote_fm_port'        : '5557',
        'volume_units'          : 'uL',
        'components'            : ['autosampler'],
        'motors'                : 'zaber',
        'zaber_motors'          : {'x': ("/dev/ttyUSB0", 1, 150),
                                   'y': ("/dev/ttyUSB0", 2, 150),
                                   'z': ("/dev/ttyUSB0", 3, 75)},
        'motor_home_velocity'   : {'x': 10, 'y': 10, 'z': 10},
        'motor_velocity'        : {'x': 75, 'y': 75, 'z': 75},
        'motor_acceleration'    : {'x': 500, 'y': 500, 'z': 500},
        'base_position'         : {'x': 7.2, 'y': 45.5, 'z': 75},
        'clean_position'        : {'x': 143, 'y': 3.5, 'z': 52},
        'out_position'          : {'x': 0, 'y': 0, 'z': 0},
        'plate_type'            : 'Thermo-Fast 96 well PCR',
        'chiller_top_on'        : False,
        # 'plate_type'            : 'Abgene 96 well deepwell storage',
        'valves'                : 'rheodyne',
        'rheodyne_valves'       : {'injection': ("/dev/ttyUSB3", 2),
                                   'sample': ("/dev/ttyUSB2", 6),
                                   'buffer': ("/dev/ttyUSB4", 6),
                                   'bypass': ("/dev/ttyUSB5", 2),
                                   'autosampler': ("/dev/ttyUSB1", 6)},
        'valve_positions'       : {'injection': 1,
                                   'sample': 1,
                                   'buffer': 1,
                                   'bypass': 1,
                                   'autosampler': 1},
        'clean_buffer_seq'      : [(3, 10), (4, 10), (3, 10), (5, 10), (6, 30)], #A set of (x, y) where x is valve position and y is time on that position
        'clean_sample_seq'      : [(3, 10), (4, 10), (3, 10), (5, 10), (6, 30)], #A set of (x, y) where x is valve position and y is time on that position
        }


    #Note, on linux to access serial ports must first sudo chmod 666 /dev/ttyUSB*
    my_autosampler = Autosampler(settings)

    # app = wx.App()

    # # standard_paths = wx.StandardPaths.Get() #Can't do this until you start the wx app
    # # info_dir = standard_paths.GetUserLocalDataDir()

    # # if not os.path.exists(info_dir):
    # #     os.mkdir(info_dir)
    # # # if not os.path.exists(os.path.join(info_dir, 'expcon.log')):
    # # #     open(os.path.join(info_dir, 'expcon.log'), 'w')
    # # h2 = handlers.RotatingFileHandler(os.path.join(info_dir, 'expcon.log'), maxBytes=10e6, backupCount=5, delay=True)
    # # h2.setLevel(logging.DEBUG)
    # # formatter2 = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    # # h2.setFormatter(formatter2)

    # # logger.addHandler(h2)

    # logger.debug('Setting up wx app')
    # frame = AutosamplerFrame(settings, None, title='Autosampler Control')
    # frame.Show()
    # app.MainLoop()

