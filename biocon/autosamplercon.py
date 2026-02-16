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
# import zaber.serial as zaber
from six import string_types
try:
    import epics
except Exception:
    pass
try:
    import motorcon
except Exception:
    pass

import valvecon
import pumpcon
import utils

class WellPlate(object):

    def __init__(self, plate_type):

        self.plate_params = copy.copy(known_well_plates)

        #Values for height depend on well. 0.8 for A1, 1.0 for H1, 0.8 for A12, 1.0 for H12
        self.x_slope = 0
        self.y_slope = 0
        # self.y_slope = 0.20/7.

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
        self.plate_height = self.plate_params[self.plate_type]['plate_height']

    def get_relative_well_position(self, row, column):
        """
        Expects column and row to be 1 indexed, like on a plate!
        """
        if isinstance(row, string_types):
            row = ord(row.lower()) - 96

        column = int(column)

        if row > self.num_rows or column > self.num_columns or row < 1 or column < 1:
            raise ValueError('Invalid row or column')

        column = int(column)-1
        row = int(row)-1

        return np.array([column*(self.col_step), row*(self.row_step),
            (self.height+column*self.x_slope+row*self.y_slope)], dtype=np.float64)

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


known_well_plates = {
    'Abgene 96 well deepwell storage' : {
        'max_volume'    : 800, # uL
        'num_columns'   : 12,
        'num_rows'      : 8,
        'col_step'      : 9.00, # mm
        'row_step'      : 9.00, # mm
        'height'        : 1, # bottom of well from chiller base plate
        'plate_height'  : 10, # top of plate from chiller base plate
        },

    'Thermo-Fast 96 well PCR' : {
        'max_volume'    : 200, # uL
        'num_columns'   : 12,
        'num_rows'      : 8,
        'col_step'      : 9.00, # mm
        'row_step'      : 9.00, # mm
        'height'        : 0.5, # bottom of well from chiller base plate
        'plate_height'  : 15.5, # top of plate from chiller base plate
        }
}

class Autosampler(object):

    def __init__(self, name, device, settings={}):

        self.name = name
        self.device = device #Note: None, here for consistency with other devices
        self.settings = settings
        self.device_settings = self.settings['device_data']['kwargs']

        self._active_count = 0

        self.abort_event = threading.Event()
        self.abort_event.clear()

        self._status = 'Idle'

        self.set_clean_offsets(self.settings['clean_offsets']['plate_x'],
            self.settings['clean_offsets']['plate_z'],
            self.settings['clean_offsets']['needle_y'])

        self._cmd_stop_event = threading.Event()
        self._cmd_queue = deque()
        self._cmd_thread = threading.Thread(target=self._run_cmds)
        self._cmd_thread.daemon = True
        self._cmd_thread.start()
        self._cmd_errors = 0

    def _run_cmds(self):
        while not self._cmd_stop_event.is_set():
            if len(self._cmd_queue) > 0:
                cmd_func, args, kwargs = self._cmd_queue.popleft()

            else:
                cmd_func = None

            if self._cmd_stop_event.is_set():
                break

            cmds_run = False

            if cmd_func is not None:
                try:
                    cmd_func(*args, **kwargs)
                    self._cmd_errors = 0
                except Exception:
                    msg = ("Autosampler %s failed to run command '%s' "
                        "with args: %s and kwargs: %s " %(self.name, command,
                        ', '.join(['{}'.format(a) for a in args]),
                        ', '.join(['{}:{}'.format(kw, item) for kw, item in kwargs.items()])))
                    logger.exception(msg)

                    self._cmd_errors += 1
                    self.stop()
                    self._check_abort()
                    self._active_count = 0

                    if self._cmd_errors <= 2:
                        self.clean()
                    else:
                        self._status = 'Error'

                cmds_run = True

            if not cmds_run:
                time.sleep(0.02)

    def connect(self):
        self._init_motors()
        self._init_valves()
        self._init_pumps()

        self.set_well_plate(self.settings['plate_type'])

        return True

    def _init_motors(self):
        logger.info('Initializing autosampler motors')

        needle_args = self.device_settings['needle_motor']['args']
        needle_kwargs = self.device_settings['needle_motor']['kwargs']
        self.needle_y_motor = motorcon.EpicsMotor(self.device_settings['needle_motor']['name'],
            *needle_args, **needle_kwargs)

        plate_x_args = self.device_settings['plate_x_motor']['args']
        plate_x_kwargs = self.device_settings['plate_x_motor']['kwargs']
        self.plate_x_motor = motorcon.EpicsMotor(self.device_settings['plate_x_motor']['name'],
            *plate_x_args, **plate_x_kwargs)

        plate_z_args = self.device_settings['plate_z_motor']['args']
        plate_z_kwargs = self.device_settings['plate_z_motor']['kwargs']
        self.plate_z_motor = motorcon.EpicsMotor(self.device_settings['plate_z_motor']['name'],
            *plate_z_args, **plate_z_kwargs)

        coflow_args = self.device_settings['coflow_y_motor']['args']
        coflow_kwargs = self.device_settings['coflow_y_motor']['kwargs']
        self.coflow_y_motor = motorcon.EpicsMotor(self.device_settings['coflow_y_motor']['name'],
            *coflow_args, **coflow_kwargs)

        self.set_base_position(self.settings['base_position']['plate_x'],
            self.settings['base_position']['plate_z'],
            self.settings['base_position']['needle_y'])

        self.set_needle_in_position(self.settings['needle_in_position'])

        self.set_plate_out_position(self.settings['plate_out_position']['plate_x'],
            self.settings['plate_out_position']['plate_z'])

        self.set_plate_load_position(self.settings['plate_load_position']['plate_x'],
            self.settings['plate_load_position']['plate_z'])

        self.set_coflow_y_ref_position(self.settings['coflow_y_ref_position'])

        self.set_load_pos_y_offset(self.settings['load_pos_y_offset'])


    def _init_valves(self):
        logger.info('Initializing autosampler valves')

        device =  self.device_settings['needle_valve']['args'][0]
        needle_args = self.device_settings['needle_valve']['args'][1:]
        needle_kwargs = self.device_settings['needle_valve']['kwargs']
        self.needle_valve = valvecon.known_valves[device](self.device_settings['needle_valve']['name'],
            *needle_args, **needle_kwargs)

    def _init_pumps(self):
        logger.info('Initializing autosampler pumps')

        syringe = self.device_settings['sample_pump']['kwargs']['syringe_id']
        syringe_info = copy.deepcopy(pumpcon.known_syringes[syringe])
        self.device_settings['sample_pump']['kwargs'].update(syringe_info)

        logger.info(self.device_settings['sample_pump'])
        device = self.device_settings['sample_pump']['args'][0]
        sample_args = self.device_settings['sample_pump']['args'][1:]
        sample_kwargs = self.device_settings['sample_pump']['kwargs']
        self.sample_pump = pumpcon.known_pumps[device](self.device_settings['sample_pump']['name'],
            *sample_args, **sample_kwargs)
        self.sample_pump.units = 'uL/min'

        device = self.device_settings['clean1_pump']['args'][0]
        clean1_args = self.device_settings['clean1_pump']['args'][1:]
        clean1_kwargs = self.device_settings['clean1_pump']['kwargs']
        self.clean1_pump = pumpcon.known_pumps[device](self.device_settings['clean1_pump']['name'],
            *clean1_args, **clean1_kwargs)
        self.clean1_pump.units = 'uL/min'

        device = self.device_settings['clean2_pump']['args'][0]
        clean2_args = self.device_settings['clean2_pump']['args'][1:]
        clean2_kwargs = self.device_settings['clean2_pump']['kwargs']
        self.clean2_pump = pumpcon.known_pumps[device](self.device_settings['clean2_pump']['name'],
            *clean2_args, **clean2_kwargs)
        self.clean2_pump.units = 'uL/min'

        device = self.device_settings['clean3_pump']['args'][0]
        clean3_args = self.device_settings['clean3_pump']['args'][1:]
        clean3_kwargs = self.device_settings['clean3_pump']['kwargs']
        self.clean3_pump = pumpcon.known_pumps[device](self.device_settings['clean3_pump']['name'],
            *clean3_args, **clean3_kwargs)
        self.clean3_pump.units = 'uL/min'

        self.set_sample_draw_rate(self.settings['pump_rates']['sample'][0], 'mL/min')
        self.set_sample_dwell_time(self.settings['load_dwell_time'])

    def home_motor(self, motor_name, thread=True):
        if not thread:
            success = self._inner_home_motor(motor_name)
        else:
            self._cmd_queue.append([self._inner_home_motor, [motor_name], {}])
            success = True

        return success

    def _inner_home_motor(self, motor_name):
        self._active_count += 1
        abort = False

        if motor_name == 'needle_y':
            motor = self.needle_y_motor
        elif motor_name == 'plate_x':
            motor = self.plate_x_motor
        elif motor_name == 'plate_z':
            motor = self.plate_z_motor

        direction = self.settings['home_settings'][motor_name]['dir']
        step = self.settings['home_settings'][motor_name]['step']
        pos = self.settings['home_settings'][motor_name]['pos']

        if direction == 1:
            on_lim = motor.on_high_limit()
        else:
            on_lim = motor.on_low_limit()

        abort = self._check_abort()

        if not on_lim and not abort:
            if direction == 1:
                jog_dir = 'positive'
            else:
                jog_dir = 'negative'

            motor.jog(jog_dir, True)

        while not on_lim and not abort:
            if direction == 1:
                on_lim = motor.on_high_limit()
            else:
                on_lim = motor.on_low_limit()

            abort = self._sleep(0.02)

        motor.jog(jog_dir, False)

        move_off = -1*direction*step

        while on_lim and not abort:
            cont = self.move_motors_relative(move_off, motor_name)
            abort = not cont

            if direction == 1:
                on_lim = motor.on_high_limit()
            else:
                on_lim = motor.on_low_limit()

        if not abort:
            logger.info('Redefining motor %s position %s to %s', motor_name,
                motor.position, pos)
            motor.position = pos

        self._active_count -= 1

        return not abort

    def move_motors_absolute(self, position, motor='all', y_offset=True):
        self._active_count += 1
        abort = False

        abort = self._check_abort()

        if not abort:
            if motor == 'all':
                plate_x_pos = position[0]
                plate_y_pos = position[1]
                needle_y_pos = position[2]

                coflow_y_pos = self.coflow_y_motor.position
                if y_offset:
                    offset = coflow_y_pos - self.coflow_y_ref
                else:
                    offset = 0
                needle_y_pos += offset

                self.plate_x_motor.move_absolute(plate_x_pos)
                self.plate_z_motor.move_absolute(plate_y_pos)
                self.needle_y_motor.move_absolute(needle_y_pos)
                self._sleep(0.05)

                while (self.plate_x_motor.is_moving()
                    or self.needle_y_motor.is_moving()
                    or self.plate_z_motor.is_moving()):
                    abort = self._sleep(0.02)
                    if abort:
                        break

            elif motor == 'plate_x':
                self.plate_x_motor.move_absolute(position)
                self._sleep(0.05)

                while self.plate_x_motor.is_moving():
                    abort = self._sleep(0.02)
                    if abort:
                        break

            elif motor == 'plate_z':
                self.plate_z_motor.move_absolute(position)
                self._sleep(0.05)

                while self.plate_z_motor.is_moving():
                    abort = self._sleep(0.02)
                    if abort:
                        break

            elif motor == 'needle_y':
                coflow_y_pos = self.coflow_y_motor.position
                if y_offset:
                    offset = coflow_y_pos - self.coflow_y_ref
                else:
                    offset = 0
                position += offset

                self.needle_y_motor.move_absolute(position)
                self._sleep(0.05)

                while self.needle_y_motor.is_moving():
                    abort = self._sleep(0.02)
                    if abort:
                        break

        self._active_count -= 1

        return not abort

    def move_motors_relative(self, position, motor='all'):
        self._active_count += 1
        abort = False

        abort = self._check_abort()

        if not abort:
            if motor == 'all':
                self.plate_x_motor.move_relative(position[0])
                self.plate_z_motor.move_relative(position[1])
                self.needle_y_motor.move_relative(position[2])
                self._sleep(0.05)

                while (self.plate_x_motor.is_moving()
                    or self.needle_y_motor.is_moving()
                    or self.plate_z_motor.is_moving()):
                    abort = self._sleep(0.02)
                    if abort:
                        break

            elif motor == 'plate_x':
                self.plate_x_motor.move_relative(position)
                self._sleep(0.05)

                while self.plate_x_motor.is_moving():
                    abort = self._sleep(0.02)
                    if abort:
                        break

            elif motor == 'plate_z':
                self.plate_z_motor.move_relative(position)
                self._sleep(0.05)

                while self.plate_z_motor.is_moving():
                    abort = self._sleep(0.02)
                    if abort:
                        break

            elif motor == 'needle_y':
                self.needle_y_motor.move_relative(position)
                self._sleep(0.05)

                while self.needle_y_motor.is_moving():
                    abort = self._sleep(0.02)
                    if abort:
                        break

        self._active_count -= 1

        return not abort

    def set_motor_velocity(self, velocity, motor='all'):
        if motor == 'all':
            self.x_velocity = float(velocity[0])
            self.y_velocity = float(velocity[1])
            self.z_velocity = float(velocity[2])

            self.plate_x_motor.set_velocity(self.x_velocity)
            self.needle_y_motor.set_velocity(self.y_velocity)
            self.plate_z_motor.set_velocity(self.z_velocity)

        elif motor == 'plate_x':
            self.x_velocity = float(velocity)
            self.plate_x_motor.set_velocity(self.x_velocity)

        elif motor == 'needle_y':
            self.y_velocity = float(velocity)
            self.needle_y_motor.set_velocity(self.y_velocity)

        elif motor == 'plate_z':
            self.z_velocity = float(velocity)
            self.plate_z_motor.set_velocity(self.z_velocity)


    def set_motor_acceleration(self, accel, motor='all'):
        if motor == 'all':
            self.x_accel = float(accel[0])
            self.y_accel = float(accel[1])
            self.z_accel = float(accel[2])

            self.plate_x_motor.set_acceleration(self.x_accel)
            self.needle_y_motor.set_acceleration(self.y_accel)
            self.plate_z_motor.set_acceleration(self.z_accel)

        elif motor == 'plate_x':
            self.x_accel = float(accel)
            self.plate_x_motor.set_acceleration(self.x_accel)

        elif motor == 'needle_y':
            self.y_accel = float(accel)
            self.needle_y_motor.set_acceleration(self.y_accel)

        elif motor == 'plate_z':
            self.z_accel = float(accel)
            self.plate_z_motor.set_acceleration(self.z_accel)

    def _check_abort(self):
        if self.abort_event.is_set():
            self.plate_x_motor.stop()
            self.plate_z_motor.stop()
            self.needle_y_motor.stop()

            self.sample_pump.stop()
            self.clean1_pump.stop()
            self.clean2_pump.stop()
            self.clean3_pump.stop()

            self.set_valve_position(self.settings['clean_valve_positions']['empty'])

            self.sample_pump.set_valve_position(
                self.settings['syringe_valve_positions']['sample'])

            self.abort_event.clear()

            abort = True

        else:
            abort = False

        return abort

    def stop(self):
        if self._active_count > 0:
            self.abort_event.set()

    def set_base_position(self, plate_x, plate_z, needle_y):
        """
        This sets the base position from which relative well positions are
        calculated based on the definitions in the WellPlate class.

        This should be the position with the needle centered in the A1 well
        and tip height at the top of the lower chiller plate.

        Zero plates: Z (upsteam/downstream): to downstream limit, positive is
        downstream. X: to inboard limit, positive is outboard.
        """

        self.base_position = np.array([plate_x, plate_z, needle_y], dtype=np.float64)

        self.set_clean_position()

    def set_clean_offsets(self, clean_x, clean_z, clean_y):
        self.clean_x_off = clean_x
        self.clean_z_off = clean_z
        self.clean_y_off = clean_y

    def set_clean_position(self):
        clean_x = self.base_position[0] + self.clean_x_off
        clean_z = self.base_position[1] + self.clean_z_off
        clean_y = self.base_position[2] + self.clean_y_off

        self.clean_position = np.array([clean_x, clean_z, clean_y], dtype=np.float64)

    def set_needle_out_position(self):
        self.needle_out_position = (self.base_position[2] + self.well_plate.plate_height
            + self.settings['needle_out_offset'])

    def set_needle_in_position(self, needle_y):
        self.needle_in_position = needle_y

    def set_plate_out_position(self, plate_x_offset, plate_z_offset):
        self.plate_x_out = self.base_position[0] + plate_x_offset
        self.plate_z_out = self.base_position[1] + plate_z_offset

    def set_plate_load_position(self, plate_x, plate_z):
        self.plate_x_load = plate_x
        self.plate_z_load = plate_z

    def set_coflow_y_ref_position(self, coflow_ref):
        self.coflow_y_ref = coflow_ref

    def set_load_pos_y_offset(self, load_pos_y_offset):
        self.load_pos_y_offset = load_pos_y_offset

    def set_well_plate(self, plate_type):
        self.well_plate = WellPlate(plate_type)

        self.set_needle_out_position()

    def set_sample_draw_rate(self, draw_rate, units='uL/min'):
        draw_rate = pumpcon.convert_flow_rate(draw_rate, units, 'uL/min')

        self._sample_draw_rate = draw_rate

    def set_sample_dwell_time(self, dwell_time):
        self._sample_dwell_time = dwell_time

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

    def get_well_position(self, row, column):
        delta_position = self.well_plate.get_relative_well_position(row, column)
        well_position = self.base_position + delta_position

        well_position[2] += self.load_pos_y_offset

        return well_position

    def move_to_load(self, row, column, thread=True):
        if not thread:
            success = self._inner_move_to_load(row, column)
        else:
            self._cmd_queue.append([self._inner_move_to_load, [row, column], {}])
            success = True

        return success

    def _inner_move_to_load(self, row, column):
        self._active_count += 1
        logger.info('Moving to load position %s%s', row, column)

        success = self.move_plate_load(row, column, False)

        if self._active_count == 1:
            self._status = 'Moving needle to load'

        if success:
            well_position = self.get_well_position(row, column)
            abort = self._sleep(1)
            if not abort:
                success = self.move_motors_absolute(well_position[2], 'needle_y')
            else:
                success = False

        self._active_count -= 1

        return success

    def move_to_clean(self, thread=True):
        if not thread:
            success = self._inner_move_to_clean()
        else:
            self._cmd_queue.append([self._inner_move_to_clean, [], {}])
            success = True

        return success

    def _inner_move_to_clean(self):
        self._active_count += 1

        logger.info('Moving to clean position')

        if self._active_count == 1:
            self._status = 'Moving to clean'

        success = self.move_motors_absolute(self.needle_out_position, 'needle_y')
        if success:
            abort = self._sleep(1)
            if not abort:
                success = self.move_motors_absolute([self.clean_position[0],
                    self.clean_position[1], self.needle_out_position])
            else:
                success = False

        if success:
            abort = self._sleep(1)
            if not abort:
                success = self.move_motors_absolute(self.clean_position[2], 'needle_y')

            else:
                success = False

        self._active_count -= 1

        return success

    def move_needle_out(self, thread=True):
        if not thread:
            success = self._inner_move_needle_out()
        else:
            self._cmd_queue.append([self._inner_move_needle_out, [], {}])
            success = True

        return success

    def _inner_move_needle_out(self):
        self._active_count += 1

        logger.info('Moving needle to out position')

        if self._active_count == 1:
            self._status = 'Moving needle out'

        success = self.move_motors_absolute(self.needle_out_position, 'needle_y')

        self._active_count -= 1

        return success

    def move_needle_in(self, thread=True):
        if not thread:
            success = self._inner_move_needle_in()
        else:
            self._cmd_queue.append([self._inner_move_needle_in, [], {}])
            success = True

        return success

    def _inner_move_needle_in(self):
        self._active_count += 1

        logger.info('Moving needle to in position')

        if self._active_count == 1:
            self._status = 'Moving needle in'

        self.move_plate_out(False)

        success = self.move_motors_absolute(self.needle_in_position, 'needle_y',
            y_offset=False)

        self._active_count -= 1

        return success

    def move_plate_out(self, thread=True):
        if not thread:
            success = self._inner_move_plate_out()
        else:
            self._cmd_queue.append([self._inner_move_plate_out, [], {}])
            success = True

        return success

    def _inner_move_plate_out(self):
        self._active_count += 1

        logger.info('Moving plate to out position')

        if self._active_count == 1:
            self._status = 'Moving well plate out'

        cur_plate_x = self.plate_x_motor.position

        if cur_plate_x != self.plate_x_load:
            success = self.move_needle_out(False)
        else:
            success =  True

        if success:
            abort = self._sleep(1)
            if not abort:
                success = self.move_motors_absolute(self.plate_x_out, 'plate_x')
            else:
                success = False

        self._active_count -= 1

        return success

    def move_plate_change(self, thread=True):
        if not thread:
            success = self._inner_move_plate_change()
        else:
            self._cmd_queue.append([self._inner_move_plate_change, [], {}])
            success = True

        return success

    def _inner_move_plate_change(self):
        self._active_count += 1

        logger.info('Moving plate to change plate position')

        if self._active_count == 1:
            self._status = 'Changing well plate'

        cur_plate_x = self.plate_x_motor.position

        if cur_plate_x != self.plate_x_out:
            success = self.move_motors_absolute(self.needle_out_position, 'needle_y')
            if success:
                abort = self._sleep(1)
                success = not abort
        else:
            success = True

        if success:
            success = self.move_motors_absolute([self.plate_x_load,
                self.plate_z_load, self.needle_y_motor.position])

        self._active_count -= 1

        return success

    def move_plate_load(self, row, column, thread=True):
        if not thread:
            success = self._inner_move_plate_load(row, column)
        else:
            self._cmd_queue.append([self._inner_move_plate_load, [row, column], {}])
            success = True

        return success

    def _inner_move_plate_load(self, row, column):
        self._active_count += 1
        logger.info('Moving plate to loading position at {}{}'.format(row, column))

        if self._active_count == 1:
            self._status = 'Moving well plate to load'

        well_position = self.get_well_position(row, column)

        success = self.move_motors_absolute(self.needle_out_position, 'needle_y')

        if success:
            abort = self._check_abort()
            success = not abort
            success = self.move_motors_absolute([well_position[0], well_position[1],
                self.needle_out_position])

        self._active_count -= 1

        return success

    def set_valve_position(self, position):
        self._active_count += 1

        self.needle_valve.set_position(position)

        self._active_count -= 1

    def set_pump_aspirate_rates(self, rates, units='uL/min', pump='all'):
        rates = pumpcon.convert_flow_rate(rates, units, 'uL/min')
        if pump == 'all':
            self.sample_pump.units = 'uL/min'
            self.sample_pump.refill_rate = rates[0]
            self.clean1_pump.units = 'uL/min'
            self.clean1_pump.refill_rate = rates[1]
            self.clean2_pump.units = 'uL/min'
            self.clean2_pump.refill_rate = rates[2]
            self.clean3_pump.units = 'uL/min'
            self.clean3_pump.refill_rate = rates[3]
        elif pump == 'sample':
            self.sample_pump.units = 'uL/min'
            self.sample_pump.refill_rate = rates
        elif pump == 'clean1':
            self.clean1_pump.units = 'uL/min'
            self.clean1_pump.refill_rate = rates
        elif pump == 'clean2':
            self.clean2_pump.units = 'uL/min'
            self.clean2_pump.refill_rate = rates
        elif pump == 'clean3':
            self.clean3_pump.units = 'uL/min'
            self.clean3_pump.refill_rate = rates

    def set_pump_dispense_rates(self, rates, units='uL/min', pump='all'):
        rates = pumpcon.convert_flow_rate(rates, units, 'uL/min')
        if pump == 'all':
            self.sample_pump.units = 'uL/min'
            self.sample_pump.flow_rate = rates[0]
            self.clean1_pump.units = 'uL/min'
            self.clean1_pump.flow_rate = rates[1]
            self.clean2_pump.units = 'uL/min'
            self.clean2_pump.flow_rate = rates[2]
            self.clean3_pump.units = 'uL/min'
            self.clean3_pump.flow_rate = rates[3]
        elif pump == 'sample':
            self.sample_pump.units = 'uL/min'
            self.sample_pump.flow_rate = rates
        elif pump == 'clean1':
            self.clean1_pump.units = 'uL/min'
            self.clean1_pump.flow_rate = rates
        elif pump == 'clean2':
            self.clean2_pump.units = 'uL/min'
            self.clean2_pump.flow_rate = rates
        elif pump == 'clean3':
            self.clean3_pump.units = 'uL/min'
            self.clean3_pump.flow_rate = rates

    def set_pump_volumes(self, volumes, units='uL', pump='all'):
        volumes = pumpcon.convert_volume(volumes, units, 'uL')
        if pump == 'all':
            self.sample_pump.units = 'uL/min'
            self.sample_pump.volume = volumes[0]
            self.clean1_pump.units = 'uL/min'
            self.clean1_pump.volume = volumes[1]
            self.clean2_pump.units = 'uL/min'
            self.clean2_pump.volume = volumes[2]
            self.clean3_pump.units = 'uL/min'
            self.clean3_pump.volume = volumes[3]
        elif pump == 'sample':
            self.sample_pump.units = 'uL/min'
            self.sample_pump.volume == volumes
        elif pump == 'clean1':
            self.clean1_pump.units = 'uL/min'
            self.clean1_pump.volume = volumes
        elif pump == 'clean2':
            self.clean2_pump.units = 'uL/min'
            self.clean2_pump.volume = volumes
        elif pump == 'clean3':
            self.clean3_pump.units = 'uL/min'
            self.clean3_pump.volume = volumes

    def set_loop_volume(self, volume):
        self.loop_volume = volume

    def aspirate(self, volume, pump, units='uL', blocking=True, thread=True):
        if not thread:
            success = self._inner_aspirate(volume, pump, units, blocking)
        else:
            self._cmd_queue.append([self._inner_aspirate, [volume, pump,
                units, blocking], {}])
            success = True

        return success

    def _inner_aspirate(self, volume, pump, units='uL', blocking=True):
        self._active_count += 1
        abort = False

        if self._active_count == 1:
            self._status = 'Aspirating sample'

        if pump == 'sample':
            selected_pump = self.sample_pump
        elif pump == 'clean1':
            selected_pump = self.clean1_pump
        elif pump == 'clean2':
            selected_pump = self.clean2_pump
        elif pump == 'clean3':
            selected_pump = self.clean3_pump

        selected_pump.aspirate(volume, units=units)

        if blocking:
            while selected_pump.is_moving():
                abort = self._sleep(0.05)
                if abort:
                    break
        else:
            abort = False

        self._active_count -= 1

        return not abort

    def dispense(self, volume, pump, trigger=False, delay=15, units='uL',
        blocking=True):
        self._active_count += 1
        abort = False

        if self._active_count == 1:
            self._status = 'Dispensing sample'

        if pump == 'sample':
            selected_pump = self.sample_pump
        elif pump == 'clean1':
            selected_pump = self.clean1_pump
        elif pump == 'clean2':
            selected_pump = self.clean2_pump
        elif pump == 'clean3':
            selected_pump = self.clean3_pump

        if trigger:
            selected_pump.dispense_with_trigger(volume, delay, units)
        else:
            selected_pump.dispense(volume, units=units)

        if blocking:
            while selected_pump.is_moving():
                abort = self._sleep(0.05)

        else:
            abort = False

        self._active_count -= 1

        return not abort

    def load_sample(self, volume, row, column, units='uL', thread=True):
        if not thread:
            success = self._inner_load_sample(volume, row, column, units)
        else:
            self._cmd_queue.append([self._inner_load_sample, [volume, row, column],
                {'units': units}])
            success = True

        return success

    def _inner_load_sample(self, volume, row, column, units='uL'):
        self._active_count += 1

        logger.info("Starting sample load of %s %s from %s%s", volume, units, row, column)

        self._status = 'Loading sample'

        self.sample_pump.set_valve_position(
            self.settings['syringe_valve_positions']['sample'])

        while self.sample_pump.is_moving():
            abort = self._sleep(0.02)
            if abort:
                break

        success = self.move_to_load(row, column, False)

        if success:
            self.set_pump_aspirate_rates(self._sample_draw_rate, 'uL/min', 'sample')
            success = self.aspirate(volume, 'sample', units, thread=False)

        if success:
            abort = self._sleep(self._sample_dwell_time)
            if not abort:
                success = self.move_needle_out(False)
            else:
                success = False

        self._active_count -= 1

        logger.info("Sample load finished")

        return success

    def move_to_inject(self, thread=True):
        if not thread:
            success = self._inner_move_to_inject()
        else:
            self._cmd_queue.append([self._inner_move_to_inject, [], {}])
            success = True

        return success

    def _inner_move_to_inject(self):
        self._active_count += 1

        logger.info("Moving needle to inject position")

        self._status = 'Moving to inject'

        self.sample_pump.set_valve_position(
            self.settings['syringe_valve_positions']['sample'])

        while self.sample_pump.is_moving():
            abort = self._sleep(0.02)
            if abort:
                break

        if self.settings['inject_connect_vol'] > 0:
            self.set_pump_dispense_rates(self.settings['inject_connect_rate'],
                'uL/min', 'sample')
            success = self.dispense(self.settings['inject_connect_vol'],
                'sample', units='uL')
        else:
            success = True

        if success:
            success = self.move_needle_in(False)

        self._active_count -= 1

        logger.info("Needle in inject position")

        return success

    def inject_sample(self, volume, rate, trigger, start_delay, end_delay,
        vol_units='uL', rate_units='uL/min', thread=True):
        if not thread:
            success = self._inner_inject_sample(volume, rate, trigger,
                start_delay, end_delay, vol_units, rate_units)
        else:
            self._cmd_queue.append([self._inner_inject_sample, [volume, rate,
                trigger, start_delay, end_delay], {'vol_units': vol_units,
                'rate_units': rate_units}])
            success = True

        return success

    def _inner_inject_sample(self, volume, rate, trigger, start_delay, end_delay,
        vol_units='uL', rate_units='uL/min'):
        #Flow rates ideally 100-200 uL/min?
        logger.info('Injecting sample')

        self._active_count += 1

        self._status = 'Injecting sample'

        self.sample_pump.set_valve_position(
            self.settings['syringe_valve_positions']['sample'])

        while self.sample_pump.is_moving():
            abort = self._sleep(0.02)
            if abort:
                break
        self.set_pump_dispense_rates(rate, rate_units, 'sample')

        load_vol = pumpcon.convert_volume(volume, vol_units, 'uL')

        self.dispense(load_vol - self.settings['reserve_vol'], 'sample',
            trigger=trigger, delay=start_delay, units='uL', blocking=False)

        abort = False

        while not self.sample_pump.is_moving():
            self._sleep(0.02)

        self._active_count += 1
        while self.sample_pump.is_moving():
            abort = self._sleep(0.02)
            if abort:
                break

        start_time = time.time()

        while time.time() - start_time < end_delay:
            abort = self._sleep(0.02)
            if abort:
                break

        self._active_count -= 1

        self._active_count -= 1

        logger.info('Injection finished')

        return not abort

    def load_and_inject(self, volume, rate, row, column, trigger, start_delay,
        end_delay, clean_needle, vol_units='uL', rate_units='uL/min', thread=True):
        if not thread:
            success = self._inner_load_and_inject(volume, rate, row, column,
                trigger, start_delay, end_delay, clean_needle, vol_units, rate_units)
        else:
            self._cmd_queue.append([self._inner_load_and_inject, [volume, rate,
                row, column, trigger, start_delay, end_delay, clean_needle], {'vol_units': vol_units,
                'rate_units': rate_units}])
            success = True

        return success

    def _inner_load_and_inject(self, volume, rate, row, column, trigger, start_delay,
        end_delay, clean_needle, vol_units='uL', rate_units='uL/min'):
        initial_vol = pumpcon.convert_volume(volume, vol_units, 'uL')

        self._active_count += 1

        success = self.load_sample(initial_vol, row, column, 'uL', False)

        if success:
            success = self.move_to_inject(False)

            if success:
                remaining_vol = initial_vol - self.settings['inject_connect_vol']
                success = self.inject_sample(remaining_vol, rate, trigger,
                    start_delay, end_delay, 'uL', rate_units, False)

                if success:
                    if clean_needle:
                        self.clean(False)

        self._active_count -= 1

        return success

    def load_and_move_to_inject(self, volume, row, column, vol_units='uL',
        rate_units='uL/min', thread=True):
        if not thread:
            success = self._inner_load_and_move_to_inject(volume, row,
                column, vol_units, rate_units)
        else:
            self._cmd_queue.append([self._inner_load_and_move_to_inject, [volume,
                row, column], {'vol_units': vol_units,
                'rate_units': rate_units}])
            success = True

        return success

    def _inner_load_and_move_to_inject(self, volume, row, column, vol_units='uL',
        rate_units='uL/min'):
        initial_vol = pumpcon.convert_volume(volume, vol_units, 'uL')

        self._active_count += 1

        success = self.load_sample(initial_vol, row, column, 'uL', False)

        if success:
            success = self.move_to_inject(False)

        self._active_count -= 1

        return success

    def clean(self, thread=True):
        if not thread:
            success = self._inner_clean()
        else:
            self._cmd_queue.append([self._inner_clean, [], {}])
            success = True

        return success

    def _inner_clean(self):
        self._active_count += 1
        logger.info('Starting cleaning sequence')

        self._status = 'Cleaning needle'

        success = self.move_to_clean(False)

        self.sample_pump.set_valve_position(
            self.settings['syringe_valve_positions']['purge'])

        while self.sample_pump.is_moving():
            abort = self._sleep(0.02)
            if abort:
                break

        rate = self.settings['pump_rates']['purge'][1]
        self.set_pump_dispense_rates(rate, 'mL/min', 'sample')
        self.sample_pump.dispense_all(blocking=False)

        while self.sample_pump.is_moving():
            abort = self._sleep(0.02)
            if abort:
                success = not abort
                break

        self.sample_pump.set_valve_position(
            self.settings['syringe_valve_positions']['clean'])

        if success:
            for clean_step in self.settings['clean_seq']:
                pump = clean_step[0]
                cmd = clean_step[1]
                self.set_valve_position(self.settings['clean_valve_positions'][pump])

                if cmd == 'dispense':
                    rate = clean_step[2]
                    vol = clean_step[3]
                    self.set_pump_dispense_rates(rate, 'mL/min', pump)
                    success = self.dispense(vol, pump, units='mL')

                elif cmd == 'wait':
                    logger.info('Drying needle')
                    wait_time = clean_step[2]
                    abort = self._sleep(wait_time)
                    success = not abort

                elif cmd == 'move_y':
                    dist = clean_step[2]
                    success = self.move_motors_relative(dist, 'needle_y')

                elif cmd == 'move_x':
                    dist = clean_step[2]
                    success = self.move_motors_relative(dist, 'plate_x')

                elif cmd == 'move_z':
                    dist = clean_step[2]
                    success = self.move_motors_relative(dist, 'plate_z')

                if not success:
                    break

            self.set_valve_position(self.settings['clean_valve_positions']['empty'])

        if success:
            self.sample_pump.set_valve_position('Input')

            self.move_needle_out(False)

        self._active_count -= 1

        return success

    def get_status(self):
        if self._active_count == 0 and self._status != 'Error':
            self._status = 'Idle'
        elif self._active_count > 0 and self._status == 'Idle':
            self._status = 'Busy'

        return self._status

    def _sleep(self, sleep_time):
        start = time.time()

        while time.time() - start < sleep_time:
            time.sleep(0.01)
            abort = self._check_abort()
            if abort:
                break

        return abort

    def on_disconnect(self):
        self._cmd_stop_event.set()
        self._cmd_thread.join(5)


class ASCommThread(utils.CommManager):

    def __init__(self, name):
        utils.CommManager.__init__(self, name)

        self._commands = {
            'connect'               : self._connect_device,
            'disconnect'            : self._disconnect_device,
            'get_well_volume'       : self._get_well_volume,
            'get_all_well_volumes'  : self._get_all_well_volumes,
            'set_well_plate'        : self._set_well_plate,
            'set_well_volume'       : self._set_well_volume,
            'set_all_well_volumes'  : self._set_all_well_volumes,
            'move_to_load'          : self._move_to_load,
            'move_to_clean'         : self._move_to_clean,
            'move_needle_out'       : self._move_needle_out,
            'move_needle_in'        : self._move_needle_in,
            'move_plate_out'        : self._move_plate_out,
            'move_plate_change'     : self._move_plate_change,
            'move_plate_load'       : self._move_plate_load,
            'home_motor'            : self._home_motor,
            'set_valve_position'    : self._set_valve_position,
            'set_sample_pump_valve' : self._set_sample_pump_valve,
            'set_aspirate_rates'    : self._set_pump_aspirate_rates,
            'set_dispense_rates'    : self._set_pump_dispense_rates,
            'set_pump_volumes'      : self._set_pump_volumes,
            'set_sample_draw_rate'  : self._set_sample_draw_rate,
            'set_sample_dwell_time' : self._set_sample_dwell_time,
            'pump_aspirate'         : self._pump_aspirate,
            'pump_dispense'         : self._pump_dispense,
            'load_sample'           : self._load_sample,
            'move_to_inject'        : self._move_to_inject,
            'inject_sample'         : self._inject_sample,
            'load_and_inject'       : self._load_and_inject,
            'load_and_move_to_inject': self._load_and_move_to_inject,
            'clean'                 : self._clean,
            'get_status'            : self._get_status,
            'stop'                  : self._stop_autosampler,
        }

        self._connected_devices = OrderedDict()
        self._connected_coms = OrderedDict()

        self.known_devices = {
            'Autosampler' : Autosampler,
            }

    def _additional_new_comm(self, name):
        pass

    def _additional_connect_device(self, name, device_type, device, **kwargs):
        pass

    def _cleanup_devices(self):
        for device in self._connected_devices.values():
            device.on_disconnect()

    def _get_well_volume(self, name, row, column, **kwargs):

        logger.debug("Getting %s well %s%s volume", name, row, column)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_well_volume(row, column)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("%s well %s%s volume is %f", name, row, column, val)

    def _get_all_well_volumes(self, name, **kwargs):

        logger.debug("Getting %s all well volumes", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_all_well_volumes()

        self._return_value((name, cmd, val), comm_name)

        logger.debug("%s got all well volumes", name)

    def _set_well_plate(self, name, val, **kwargs):
        logger.debug("Setting %s well plate to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_well_plate(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)
        self._return_value((name, cmd, val), 'status')

        logger.debug("%s well plate set", name)

    def _set_well_volume(self, name, val, row, column, **kwargs):
        logger.debug("Setting %s well %s%s volume to %s", name,
            row, column, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_well_volume(val, row, column, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s well volume set", name)

    def _set_all_well_volumes(self, name, val, **kwargs):
        logger.debug("Setting %s all well %s%s volumes to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_all_well_volume(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s all well volumes set", name)

    def _move_to_load(self, name, row, column, **kwargs):
        logger.debug("%s moving to load position", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.move_to_load(row, column, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s moved to load position", name)

    def _move_to_clean(self, name, **kwargs):
        logger.debug("%s moving to clean position", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.move_to_clean(**kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s moved to clean position", name)

    def _move_needle_out(self, name, **kwargs):
        logger.debug("%s moving needle out", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.move_needle_out(**kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s moved needle out", name)

    def _move_needle_in(self, name, **kwargs):
        logger.debug("%s moving needle in", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.move_needle_in(**kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s moved needle in", name)

    def _move_plate_out(self, name, **kwargs):
        logger.debug("%s moving plate out", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.move_plate_out(**kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s moved plate out", name)

    def _move_plate_change(self, name, **kwargs):
        logger.debug("%s moving plate to change plate position", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.move_plate_change(**kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s moved plate to change plate position", name)

    def _move_plate_load(self, name, row, col, **kwargs):
        logger.debug("%s moving plate to well %s%s load position", name,
            row, col)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.move_plate_load(row, col, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s moved plate to well %s%s load position", name,
            row, col)

    def _home_motor(self, name, motor_name, **kwargs):
        logger.info("%s homing motor %s", name, motor_name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.home_motor(motor_name, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s homed motor %s", name, motor_name)

    def _set_valve_position(self, name, val, **kwargs):
        logger.info("Setting %s valve position to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_valve_position(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s valve position set", name)

    def _set_sample_pump_valve(self, name, val, **kwargs):
        logger.info("Setting %s sample pump valve position to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.sample_pump.set_valve_position(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s valve position set", name)

    def _set_pump_aspirate_rates(self, name, val, units, pump, **kwargs):
        logger.info("Setting %s pump %s aspirate rates", name, pump)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_pump_aspirate_rates(val, units, pump, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s pump %s aspirate rates set", name, pump)

    def _set_pump_dispense_rates(self, name, val, units, pump, **kwargs):
        logger.info("Setting %s pump %s dispense rates", name, pump)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_pump_dispense_rates(val, units, pump, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s pump %s dispense rates set", name, pump)

    def _set_sample_draw_rate(self, name, val, units, **kwargs):
        logger.info("Setting %s sample draw rate to %s %s", name, val, units)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_sample_draw_rate(val, units, **kwargs)
        device.set_pump_aspirate_rates(device._sample_draw_rate, units, 'sample')

        self._return_value((name, cmd, True), comm_name)
        self._return_value((name, cmd, val), 'status')

        logger.debug("%s sample draw rate set", name)

    def _set_sample_dwell_time(self, name, val, **kwargs):
        logger.info("Setting %s sample dwell time to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_sample_dwell_time(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)
        self._return_value((name, cmd, val), 'status')

        logger.debug("%s sample dwell time set", name)

    def _set_pump_volumes(self, name, val, units, pump, **kwargs):
        logger.info("Setting %s pump %s volumes", name, pump)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_pump_volumes(val, units, pump, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s pump %s volumes set", name, pump)

    def _pump_aspirate(self, name, val, units, pump, **kwargs):
        logger.info("%s pump %s aspriating %s %s", name, pump, val, units)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.aspirate(val, pump, units, True, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s pump %s aspriate stated", name, pump)

    def _pump_dispense(self, name, val, units, pump, trigger, delay, **kwargs):
        logger.info("%s pump %s dispensing %s %s", name, pump, val, units)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.dispense(val, pump, trigger, delay, units, False, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s pump %s dispense stated", name, pump)

    def _load_sample(self, name, val, row, column, units, **kwargs):
        logger.debug("%s loading %s %s from %s%s", name, val, units, row, column)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.load_sample(val, row, column, units, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        load_settings = {
            'volume'    : val,
            'row'       : row,
            'column'    : column,
            'vol_units' : units,
            }

        self._return_value((name, cmd, load_settings), 'status')

        logger.debug("%s loaded sample", name)

    def _move_to_inject(self, name, **kwargs):
        logger.debug("%s moving to inject position", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.move_to_inject(**kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s moved to inject position", name)

    def _inject_sample(self, name, val, rate, trigger, start_delay, end_delay,
        vol_units, rate_units, **kwargs):
        logger.debug("%s injecting sample", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.inject_sample(val, rate, trigger, start_delay,
            end_delay, vol_units, rate_units, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        inj_settings = {
            'volume'        : val,
            'rate'          : rate,
            'trigger'       : trigger,
            'start_delay'   : start_delay,
            'end_delay'     : end_delay,
            'vol_units'     : vol_units,
            'rate_units'    : rate_units,
            }

        self._return_value((name, cmd, inj_settings), 'status')

        logger.debug("%s injected sample", name)

    def _load_and_inject(self, name, val, rate, row, column, trigger,
        start_delay, end_delay, vol_units, rate_units, clean_needle, **kwargs):
        logger.debug("%s loading and injecting sample", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.load_and_inject(val, rate, row, column, trigger,
            start_delay, end_delay, clean_needle, vol_units, rate_units, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        inj_settings = {
            'volume'        : val,
            'rate'          : rate,
            'row'           : row,
            'column'        : column,
            'trigger'       : trigger,
            'start_delay'   : start_delay,
            'end_delay'     : end_delay,
            'vol_units'     : vol_units,
            'rate_units'    : rate_units,
            }
        self._return_value((name, cmd, inj_settings), 'status')

        logger.debug("%s loaded and injected sample", name)

    def _load_and_move_to_inject(self, name, val, row, column, vol_units,
        rate_units, **kwargs):
        logger.debug("%s loading and injecting sample", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.load_and_move_to_inject(val, row, column,
            vol_units, rate_units, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        load_settings = {
            'volume'    : val,
            'row'       : row,
            'column'    : column,
            'vol_units' : vol_units,
            }

        self._return_value((name, cmd, load_settings), 'status')

        logger.debug("%s loaded and injected sample", name)

    def _clean(self, name, **kwargs):
        logger.debug("%s injecting sample", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.clean(**kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s injected sample", name)

    def _get_status(self, name, **kwargs):
        logger.debug("Getting %s status", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_status()

        self._return_value((name, cmd, val), comm_name)

        logger.debug("%s status is %s", name, val)

    def _stop_autosampler(self, name, **kwargs):
        logger.info("Stopping %s Autosampler", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.stop()

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s stopped", name)

    def _additional_abort(self):
        for name in self._connected_devices:
            device = self._connected_devices[name]
            device.stop()



def make_well_plate_layout(top_level, parent, well_bmp, on_well_button):
    col_labels = ['{}'.format(col) for col in range(1,13)]
    row_labels = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
    row_idx = -1
    col_idx = 0
    wells = ['{}{}'.format(row, col) for row in row_labels for col in col_labels]
    well_ids_96 = {well :wx.NewIdRef() for well in wells}
    reverse_well_ids_96 = {wid: well for (well, wid) in well_ids_96.items()}

    well_box = wx.StaticBox(parent, label='Well Plate')

    well_plate_96 = wx.FlexGridSizer(cols=13, rows=9, hgap=top_level._FromDIP(0),
        vgap=top_level._FromDIP(0))
    well_plate_96.AddSpacer(1)

    for col in col_labels:
        well_plate_96.Add(wx.StaticText(well_box, label=col),
            flag=wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_CENTER_VERTICAL)

    for i in range(104):
        if i % 13 == 0:
            row_idx += 1
            col_idx = 0
            row = row_labels[row_idx]

            well_plate_96.Add(wx.StaticText(well_box, label=row),
                flag=wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_CENTER_VERTICAL)

        else:
            row = row_labels[row_idx]
            col = col_labels[col_idx]
            col_idx += 1

            btn_id = well_ids_96['{}{}'.format(row, col)]

            well_btn = wx.BitmapButton(well_box, btn_id, well_bmp,
                style=wx.NO_BORDER)
            well_btn.SetToolTip('{}{}'.format(row, col))
            well_btn.Bind(wx.EVT_BUTTON, on_well_button)
            well_plate_96.Add(well_btn,
                flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)

    well_sizer = wx.StaticBoxSizer(well_box, wx.HORIZONTAL)
    well_sizer.Add(well_plate_96,flag=wx.ALL, border=top_level._FromDIP(5))

    return well_sizer, well_ids_96, reverse_well_ids_96


class AutosamplerPanel(utils.DevicePanel):

    def __init__(self, parent, panel_id, settings, *args, **kwargs):
        try:
            biocon = wx.FindWindowByName('biocon')
        except Exception:
            biocon = None

        if biocon is not None:
            settings['device_data'] = settings['device_init'][0]

        if settings['device_communication'] == 'remote':
            settings['remote'] = True
        else:
            settings['remote'] = False

        self._selected_well = ''
        self._current_inj_rate = 0.
        self._current_draw_rate = 0.
        self._current_dwell_time = 0.
        self._current_well_plate_type = ''
        self._current_load_volume = 0.
        self._current_buffer_start_delay = 0.
        self._current_buffer_end_delay = 0.
        self._current_status = ''
        self._current_trigger_on_inject = True

        self._staff_ctrl_window = None

        super(AutosamplerPanel, self).__init__(parent, panel_id, settings, *args, **kwargs)

    def _create_layout(self):
        """Creates the layout for the panel."""
        parent = self

        self.well_bmp = utils.load_DIP_bitmap('./resources/icons8-circled-thin-28.png',
            wx.BITMAP_TYPE_PNG, False)['light']
        self.selected_well_bmp = utils.load_DIP_bitmap('./resources/sel_icons8-circled-thin-28.png',
            wx.BITMAP_TYPE_PNG, False)['light']

        basic_ctrl_sizer = self._make_basic_controls(parent)

        adv_ctrl_sizer = self._make_advanced_controls(parent)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(basic_ctrl_sizer, flag=wx.ALL, border=self._FromDIP(5))
        top_sizer.Add(adv_ctrl_sizer, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))

        self.SetSizer(top_sizer)

        self._ctrl_btns = [
            self.load_and_inject_btn,
            self.change_plate_btn,
            self.move_plate_out_btn,
            self.move_plate_well_btn,
            self.move_needle_load,
            self.move_needle_clean,
            self.move_needle_in,
            self.move_needle_out,
            self.clean_btn,
            self.move_to_inject,
            self.inject_sample,
            self.aspirate_sample,
            self.plate_types,
            ]

    def _make_basic_controls(self, parent):
        as_box = wx.StaticBox(parent, label="Autosampler controls")

        self.status = wx.StaticText(as_box, size=self._FromDIP((100, -1)),
            style=wx.ST_NO_AUTORESIZE)
        self.sample_well = wx.StaticText(as_box, size=self._FromDIP((40,-1)),
                style=wx.ST_NO_AUTORESIZE)
        self.load_volume = wx.TextCtrl(as_box, validator=utils.CharValidator('float'))
        self.buffer_start_delay = wx.TextCtrl(as_box, validator=utils.CharValidator('float'))
        self.buffer_end_delay = wx.TextCtrl(as_box, validator=utils.CharValidator('float'))

        self.load_and_inject_btn = wx.Button(as_box, label='Load and inject')
        self.load_and_inject_btn.Bind(wx.EVT_BUTTON, self._on_load_and_inject)
        self.change_plate_btn = wx.Button(as_box, label='Change plate')
        self.change_plate_btn.Bind(wx.EVT_BUTTON, self._on_change_plate)
        self.stop_btn = wx.Button(as_box, label='Stop')
        self.stop_btn.Bind(wx.EVT_BUTTON, self._on_stop)

        status_sizer = wx.BoxSizer(wx.HORIZONTAL)
        status_sizer.Add(wx.StaticText(as_box, label='Status:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_sizer.Add(self.status, flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT,
            border=self._FromDIP(5), proportion=1)

        ctrl_sub_sizer1 = wx.FlexGridSizer(cols=2, hgap=self._FromDIP(5),
            vgap=self._FromDIP(5))
        ctrl_sub_sizer1.Add(wx.StaticText(as_box, label='Sample Well:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        ctrl_sub_sizer1.Add(self.sample_well, flag=wx.ALIGN_CENTER_VERTICAL)
        ctrl_sub_sizer1.Add(wx.StaticText(as_box, label='Load volume [uL]:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        ctrl_sub_sizer1.Add(self.load_volume, flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        ctrl_sub_sizer1.Add(wx.StaticText(as_box, label='Delay after trigger [s]:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        ctrl_sub_sizer1.Add(self.buffer_start_delay, flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        ctrl_sub_sizer1.Add(wx.StaticText(as_box, label='Delay after injection [s]:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        ctrl_sub_sizer1.Add(self.buffer_end_delay, flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        ctrl_sub_sizer1.Add(self.load_and_inject_btn, flag=wx.ALIGN_CENTER_VERTICAL)
        ctrl_sub_sizer1.Add(self.stop_btn, flag=wx.ALIGN_CENTER_VERTICAL)
        ctrl_sub_sizer1.Add(self.change_plate_btn, flag=wx.ALIGN_CENTER_VERTICAL)

        ctrl_sizer = wx.BoxSizer(wx.VERTICAL)
        ctrl_sizer.Add(status_sizer, flag=wx.EXPAND)
        ctrl_sizer.Add(ctrl_sub_sizer1, flag=wx.TOP, border=self._FromDIP(5))

        well_sizer, self.well_ids_96, self.reverse_well_ids_96 = make_well_plate_layout(
                self, as_box, self.well_bmp, self._on_well_button)

        top_sizer = wx.StaticBoxSizer(as_box, wx.HORIZONTAL)
        top_sizer.Add(ctrl_sizer, flag=wx.TOP|wx.LEFT|wx.BOTTOM, border=self._FromDIP(5))
        top_sizer.Add(well_sizer, flag=wx.ALL, border=self._FromDIP(5))

        return top_sizer

    def _make_advanced_controls(self, parent):
        if not self.settings['inline_panel']:
            pane_style = wx.CP_DEFAULT_STYLE
        else:
            pane_style = wx.CP_NO_TLW_RESIZE

        adv_pane = wx.CollapsiblePane(parent, label="Advanced settings",
            style=pane_style)
        if self.settings['inline_panel']:
            adv_pane.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, self.on_collapse)
        adv_win = adv_pane.GetPane()

        plate_box = wx.StaticBox(adv_win, label="Well plate controls")

        self.plate_types = wx.Choice(plate_box, choices=list(known_well_plates.keys()))
        self.plate_types.Bind(wx.EVT_CHOICE, self._on_change_plate_type)

        self.move_plate_out_btn = wx.Button(plate_box, label='Move out')
        self.move_plate_out_btn.Bind(wx.EVT_BUTTON, self._on_move_plate_out)
        self.move_plate_well_btn = wx.Button(plate_box, label='Move to well')
        self.move_plate_well_btn.Bind(wx.EVT_BUTTON, self._on_move_plate_well)

        plate_sub_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        plate_sub_sizer.Add(wx.StaticText(plate_box, label='Well plate type:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        plate_sub_sizer.Add(self.plate_types, flag=wx.ALIGN_CENTER_VERTICAL)
        plate_sub_sizer.Add(self.move_plate_out_btn, flag=wx.ALIGN_CENTER_VERTICAL)
        plate_sub_sizer.Add(self.move_plate_well_btn, flag=wx.ALIGN_CENTER_VERTICAL)

        plate_sizer = wx.StaticBoxSizer(plate_box, wx.VERTICAL)
        plate_sizer.Add(plate_sub_sizer, flag=wx.ALL, border=self._FromDIP(5))


        needle_box = wx.StaticBox(adv_win, label='Needle controls')

        self.move_needle_load = wx.Button(needle_box, label='Move to load')
        self.move_needle_load.Bind(wx.EVT_BUTTON, self._on_move_needle_load)
        self.move_needle_clean = wx.Button(needle_box, label='Move to clean')
        self.move_needle_clean.Bind(wx.EVT_BUTTON, self._on_move_needle_clean)
        self.move_needle_in = wx.Button(needle_box, label='Move in')
        self.move_needle_in.Bind(wx.EVT_BUTTON, self._on_move_needle_in)
        self.move_needle_out = wx.Button(needle_box, label='Move out')
        self.move_needle_out.Bind(wx.EVT_BUTTON, self._on_move_needle_out)
        self.clean_btn = wx.Button(needle_box, label='Clean needle')
        self.clean_btn.Bind(wx.EVT_BUTTON, self._on_clean)

        needle_sub_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        needle_sub_sizer.Add(self.clean_btn, flag=wx.ALIGN_CENTER_VERTICAL)
        needle_sub_sizer.Add(self.move_needle_load, flag=wx.ALIGN_CENTER_VERTICAL)
        needle_sub_sizer.Add(self.move_needle_clean, flag=wx.ALIGN_CENTER_VERTICAL)
        needle_sub_sizer.Add(self.move_needle_in, flag=wx.ALIGN_CENTER_VERTICAL)
        needle_sub_sizer.Add(self.move_needle_out, flag=wx.ALIGN_CENTER_VERTICAL)

        needle_sizer = wx.StaticBoxSizer(needle_box, wx.VERTICAL)
        needle_sizer.Add(needle_sub_sizer, flag=wx.ALL, border=self._FromDIP(5))


        inj_box = wx.StaticBox(adv_win, label='Injection controls')

        self.inj_rate = wx.TextCtrl(inj_box, validator=utils.CharValidator('float'),
            size=self._FromDIP((80,-1)))
        self.clean_after_inject = wx.CheckBox(inj_box, label='Clean needle after inject')
        self.clean_after_inject.SetValue(True)
        self.move_to_inject = wx.Button(inj_box, label='Move to inject')
        self.move_to_inject.Bind(wx.EVT_BUTTON, self._on_move_to_inject)
        self.inject_sample = wx.Button(inj_box, label='Inject')
        self.inject_sample.Bind(wx.EVT_BUTTON, self._on_inject_sample)
        self.trigger_on_inject = wx.CheckBox(inj_box, label='Trigger on injection')
        self.trigger_on_inject.SetValue(True)

        inj_sub_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        inj_sub_sizer.Add(wx.StaticText(inj_box, label='Injection rate [uL/min]:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        inj_sub_sizer.Add(self.inj_rate, flag=wx.ALIGN_CENTER_VERTICAL)
        inj_sub_sizer.Add(self.clean_after_inject, flag=wx.ALIGN_CENTER_VERTICAL)
        inj_sub_sizer.Add(self.trigger_on_inject, flag=wx.ALIGN_CENTER_VERTICAL)
        inj_sub_sizer.Add(self.move_to_inject, flag=wx.ALIGN_CENTER_VERTICAL)
        inj_sub_sizer.Add(self.inject_sample, flag=wx.ALIGN_CENTER_VERTICAL)

        inj_sizer = wx.StaticBoxSizer(inj_box, wx.VERTICAL)
        inj_sizer.Add(inj_sub_sizer, flag=wx.ALL, border=self._FromDIP(5))


        pump_box = wx.StaticBox(adv_win, label='Pump controls')

        self.draw_rate = wx.TextCtrl(pump_box, validator=utils.CharValidator('float'),
            size=self._FromDIP((80,-1)))
        self.dwell_time = wx.TextCtrl(pump_box, validator=utils.CharValidator('float'),
            size=self._FromDIP((80,-1)))
        self.aspirate_sample = wx.Button(pump_box, label='Aspirate Sample')

        self.aspirate_sample.Bind(wx.EVT_BUTTON, self._on_aspirate_sample)

        pump_sub_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        pump_sub_sizer.Add(wx.StaticText(pump_box, label='Sample draw rate [uL/min]:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump_sub_sizer.Add(self.draw_rate, flag=wx.ALIGN_CENTER_VERTICAL)
        pump_sub_sizer.Add(wx.StaticText(pump_box, label='Wait time after draw [s]:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump_sub_sizer.Add(self.dwell_time, flag=wx.ALIGN_CENTER_VERTICAL)

        pump_sizer = wx.StaticBoxSizer(pump_box, wx.VERTICAL)
        pump_sizer.Add(pump_sub_sizer, flag=wx.ALL, border=self._FromDIP(5))
        pump_sizer.Add(self.aspirate_sample, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))


        self._staff_ctrl_btn = wx.Button(adv_win, label='Staff Controls')
        self._staff_ctrl_btn.Bind(wx.EVT_BUTTON, self._on_staff_ctrl_btn)


        top_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        top_sizer.Add(inj_sizer)
        top_sizer.Add(needle_sizer)
        top_sizer.Add(plate_sizer)
        top_sizer.Add(pump_sizer)
        top_sizer.Add(self._staff_ctrl_btn)

        adv_win.SetSizer(top_sizer)

        return adv_pane

    def on_collapse(self, event):
        self.Layout()
        self.Fit()

        self.parent.Layout()
        self.parent.Fit()

        try:
            wx.FindWindowByName('biocon').Layout()
            wx.FindWindowByName('biocon').Fit()
        except Exception:
            pass

    def _on_well_button(self, evt):
        ctrl_id = evt.GetId()

        well = self.reverse_well_ids_96[ctrl_id]

        self._set_selected_well(well)

    def _set_selected_well(self, well):
        if self._selected_well in self.well_ids_96:
            old_ctrl = wx.FindWindowById(self.well_ids_96[self._selected_well])
            old_ctrl.SetBitmap(self.well_bmp)

        new_ctrl = wx.FindWindowById(self.well_ids_96[well])

        new_ctrl.SetBitmap(self.selected_well_bmp)
        self._selected_well = well

        self.sample_well.SetLabel(self._selected_well)


    def _on_load_and_inject(self, evt):
        self._prepare_load_and_inject(True)

    def get_load_and_inject_settings(self):
        well = self._selected_well
        volume = self.load_volume.GetValue()
        rate = self.inj_rate.GetValue()
        start_delay = self.buffer_start_delay.GetValue()
        end_delay = self.buffer_end_delay.GetValue()
        trigger = self.trigger_on_inject.GetValue()
        vol_units = 'uL'
        rate_units = 'uL/min'
        draw_rate = self.draw_rate.GetValue()
        dwell_time = self.dwell_time.GetValue()
        clean_needle = self.clean_after_inject.GetValue()

        settings = {
            'sample_well'   : well,
            'volume'        : volume,
            'rate'          : rate,
            'start_delay'   : start_delay,
            'end_delay'     : end_delay,
            'trigger'       : trigger,
            'vol_units'     : vol_units,
            'rate_units'    : rate_units,
            'draw_rate'     : draw_rate,
            'dwell_time'    : dwell_time,
            'clean_needle'  : clean_needle,
        }

        return settings

    def _prepare_load_and_inject(self, verbose):
        settings = self.get_load_and_inject_settings()

        well = settings['sample_well']
        volume = settings['volume']
        rate = settings['rate']
        start_delay = settings['start_delay']
        end_delay = settings['end_delay']
        trigger = settings['trigger']
        vol_units = settings['vol_units']
        rate_units = settings['rate_units']
        draw_rate = settings['draw_rate']
        dwell_time = settings['dwell_time']
        clean_needle = settings['clean_needle']

        (row, col, volume, rate, start_delay, end_delay, trigger, vol_units,
            rate_units, draw_rate, dwell_time, errors) = self.validate_load_and_inject_params(
            well, volume, rate, start_delay, end_delay, trigger, vol_units,
            rate_units, draw_rate, dwell_time, verbose)

        if len(errors) == 0:
            self._load_and_inject(row, col, volume, rate, start_delay,
                end_delay, trigger, vol_units, rate_units, draw_rate,
                dwell_time, clean_needle)

    def _load_and_inject(self, row, col, volume, rate, start_delay, end_delay,
        trigger, vol_units, rate_units, draw_rate, dwell_time, clean_needle):
        rate_cmd = ['set_sample_draw_rate', [self.name, draw_rate, rate_units], {}]
        dwell_cmd = ['set_sample_dwell_time', [self.name, dwell_time,], {}]

        inj_cmd = ['load_and_inject', [self.name, volume, rate, row, col,
            trigger, start_delay, end_delay, vol_units, rate_units, clean_needle], {}]

        self._send_cmd(rate_cmd, False)
        self._send_cmd(dwell_cmd, False)
        self._send_cmd(inj_cmd, False)

    def validate_load_and_inject_params(self, well, volume, rate, start_delay,
        end_delay, trigger, vol_units, rate_units, draw_rate, dwell_time, verbose):

        (volume, rate, start_delay, end_delay, trigger, vol_units, rate_units,
            errors) = self._validate_inject_params(volume, rate, start_delay,
            end_delay, trigger, vol_units, rate_units, False)

        try:
            row = well[0]
            col = well[1:]
        except Exception:
            errors.append('Selected well "{}" is not a valid well'.format(well))
            row = None
            col = None

        try:
            draw_rate = float(draw_rate)
        except Exception:
            errors.append('Sample draw rate must be a number.')
            draw_rate = None

        if isinstance(draw_rate, float):
            ul_min_draw_rate = pumpcon.convert_flow_rate(draw_rate, rate_units,
                'uL/min')
            ul_min_max_draw_rate = pumpcon.convert_flow_rate(self.settings['max_draw_rate'],
                'mL/min', 'uL/min')
            if ul_min_draw_rate <= 0:
                errors.append('Sample draw rate must be > 0 uL/min')
            elif ul_min_draw_rate > ul_min_max_draw_rate:
                errors.append('Sample draw rate must <= {} uL/min'.format(
                    ul_min_max_draw_rate))
        try:
            dwell_time = float(dwell_time)
        except Exception:
            errors.append('Wait time after draw must be a number.')
            dwell_time = None

        if isinstance(dwell_time, float):
            if dwell_time < 0:
                errors.append('Wait time after draw must be >= 0 s')

        if len(errors) >0 and verbose:
            msg = 'The following field(s) have invalid values:'
            for err in errors:
                msg = msg + '\n- ' + err
            msg = msg + ('\n\nPlease correct these errors, then load and inject.')

            wx.CallAfter(wx.MessageBox, msg, 'Error in load and inject parameters',
                style=wx.OK|wx.ICON_ERROR)

        return (row, col, volume, rate, start_delay, end_delay, trigger,
            vol_units, rate_units, draw_rate, dwell_time, errors)

    def _load_and_move_to_inject(self, row, col, volume, vol_units, rate_units,
        draw_rate, dwell_time):
        rate_cmd = ['set_sample_draw_rate', [self.name, draw_rate, rate_units], {}]
        dwell_cmd = ['set_sample_dwell_time', [self.name, dwell_time,], {}]

        load_cmd = ['load_and_move_to_inject', [self.name, volume, row, col,
            vol_units, rate_units], {}]

        self._send_cmd(rate_cmd, False)
        self._send_cmd(dwell_cmd, False)
        self._send_cmd(load_cmd, False)

    def _on_aspirate_sample(self, evt):
        self._prepare_aspirate(True)

    def _prepare_aspirate(self, verbose):
        volume = self.load_volume.GetValue()
        vol_units = 'uL'
        rate_units = 'uL/min'
        draw_rate = self.draw_rate.GetValue()
        dwell_time = self.dwell_time.GetValue()

        (volume, vol_units, rate_units, draw_rate, dwell_time, errors) = self._validate_aspirate_params(
            volume, vol_units, rate_units, draw_rate, dwell_time, verbose)

        if len(errors) == 0:
            self._aspirate(volume, vol_units, rate_units, draw_rate, dwell_time)

    def _aspirate(self, volume, vol_units, rate_units, draw_rate, dwell_time):
        rate_cmd = ['set_sample_draw_rate', [self.name, draw_rate, rate_units], {}]
        dwell_cmd = ['set_sample_dwell_time', [self.name, dwell_time,], {}]
        valve_cmd = ['set_sample_pump_valve', [self.name,
            self.settings['syringe_valve_positions']['sample']], {}]
        inj_cmd = ['pump_aspirate', [self.name, volume, vol_units, 'sample'], {}]

        self._send_cmd(rate_cmd, False)
        self._send_cmd(dwell_cmd, False)
        self._send_cmd(valve_cmd, False)
        self._send_cmd(inj_cmd, False)

    def _validate_aspirate_params(self, volume, vol_units, rate_units,
        draw_rate, dwell_time, verbose):
        errors = []

        try:
            volume = float(volume)
        except Exception:
            errors.append('Volume must be a number.')

        if isinstance(volume, float):
            ul_vol = pumpcon.convert_volume(volume, vol_units, 'uL')
            if ul_vol < self.settings['min_load_volume']:
                errors.append('Volume must be >= {} uL'.format(
                    self.settings['min_load_volume']))
            elif ul_vol > self.settings['loop_volume']:
                errors.append('Volume must be <= {} uL'.format(
                    self.settings['loop_volume']))

        try:
            draw_rate = float(draw_rate)
        except Exception:
            errors.append('Sample draw rate must be a number.')
            draw_rate = None

        if isinstance(draw_rate, float):
            ul_min_draw_rate = pumpcon.convert_flow_rate(draw_rate, rate_units,
                'uL/min')
            ul_min_max_draw_rate = pumpcon.convert_flow_rate(self.settings['max_draw_rate'],
                'mL/min', 'uL/min')
            if ul_min_draw_rate <= 0:
                errors.append('Sample draw rate must be > 0 uL/min')
            elif ul_min_draw_rate > ul_min_max_draw_rate:
                errors.append('Sample draw rate must <= {} uL/min'.format(
                    ul_min_max_draw_rate))
        try:
            dwell_time = float(dwell_time)
        except Exception:
            errors.append('Wait time after draw must be a number.')
            dwell_time = None

        if isinstance(dwell_time, float):
            if dwell_time < 0:
                errors.append('Wait time after draw must be >= 0 s')

        if len(errors) >0 and verbose:
            msg = 'The following field(s) have invalid values:'
            for err in errors:
                msg = msg + '\n- ' + err
            msg = msg + ('\n\nPlease correct these errors, then load and inject.')

            wx.CallAfter(wx.MessageBox, msg, 'Error in load and inject parameters',
                style=wx.OK|wx.ICON_ERROR)

        return (volume, vol_units, rate_units, draw_rate, dwell_time, errors)

    def _on_clean(self, evt):
        self._clean_needle()

    def _clean_needle(self):
        self._send_cmd(['clean', [self.name,], {}], False)

    def _on_change_plate(self, evt):
        self._change_plate()

    def _change_plate(self):
        self._send_cmd(['move_plate_change', [self.name,], {}], False)

        msg = ("Plate holder is in the plate change position. "
            "Once you have changed the plate press Ok to continue.")
        dlg = wx.MessageDialog(None, msg, "Change plate",
            wx.OK|wx.ICON_INFORMATION)
        dlg.ShowModal()
        dlg.Destroy()

        self._send_cmd(['move_plate_out', [self.name,], {}], False)

    def _on_stop(self, evt):
        self._abort()

    def _abort(self):
        self.com_thread._abort()

        if self.remote:
            self._send_cmd(['stop', [self.name,], {}], False)

    def _on_change_plate_type(self, evt):
        plate_type = self.plate_types.GetStringSelection()

        self._current_well_plate_type = plate_type

        self._send_cmd(['set_well_plate', [self.name, plate_type,], {}], False)

    def _on_move_plate_out(self, evt):
        self._send_cmd(['move_plate_out', [self.name,], {}], False)

    def _on_move_plate_well(self, evt):
        well = self._selected_well

        proceed = True

        try:
            row = well[0]
            col = well[1:]
        except Exception:
            error = 'Selected well "{}" is not a valid well'.format(well)
            proceed = False

        if proceed:
            self._send_cmd(['move_plate_load', [self.name, row, col,], {}], False)
        else:
            wx.CallAfter(wx.MessageBox, error, 'Invalid well selected',
                style=wx.OK|wx.ICON_ERROR)

    def _on_move_needle_in(self, evt):
        self._send_cmd(['move_needle_in', [self.name,], {}], False)

    def _on_move_needle_out(self, evt):
        self._send_cmd(['move_needle_out', [self.name,], {}], False)

    def _on_move_needle_load(self, evt):
        well = self._selected_well

        proceed = True

        try:
            row = well[0]
            col = well[1:]
        except Exception:
            error = 'Selected well "{}" is not a valid well'.format(well)
            proceed = False

        if proceed:
            self._send_cmd(['move_to_load', [self.name, row, col], {}], False)
        else:
            wx.CallAfter(wx.MessageBox, error, 'Invalid well selected',
                style=wx.OK|wx.ICON_ERROR)

    def _on_move_needle_clean(self, evt):
        self._move_to_clean()

    def _move_to_clean(self):
        self._send_cmd(['move_to_clean', [self.name,], {}], False)

    def _on_move_to_inject(self, evt):
        self._send_cmd(['move_to_inject', [self.name,], {}], False)

    def _on_inject_sample(self, evt):
        self._prepare_inject(True)

    def _prepare_inject(self, verbose):
        volume = self.load_volume.GetValue()
        rate = self.inj_rate.GetValue()
        start_delay = self.buffer_start_delay.GetValue()
        end_delay = self.buffer_end_delay.GetValue()
        trigger = self.trigger_on_inject.GetValue()
        vol_units = 'uL'
        rate_units = 'uL/min'
        clean_needle = self.clean_after_inject.GetValue()

        (volume, rate, start_delay, end_delay, trigger, vol_units, rate_units,
            errors) = self._validate_inject_params(volume, rate, start_delay,
                end_delay, trigger, vol_units, rate_units, verbose)

        if len(errors) == 0:
            self._inject(volume, rate, start_delay, end_delay, trigger,
                vol_units, rate_units, clean_needle)

    def _validate_inject_params(self, volume, rate, start_delay, end_delay,
        trigger, vol_units, rate_units, verbose):
        errors = []

        try:
            volume = float(volume)
        except Exception:
            errors.append('Volume must be a number.')

        if isinstance(volume, float):
            ul_vol = pumpcon.convert_volume(volume, vol_units, 'uL')
            if ul_vol < self.settings['min_load_volume']:
                errors.append('Volume must be >= {} uL'.format(
                    self.settings['min_load_volume']))
            elif ul_vol > self.settings['loop_volume']:
                errors.append('Volume must be <= {} uL'.format(
                    self.settings['loop_volume']))

        try:
            rate = float(rate)
        except Exception:
            errors.append('Injection rate must be a number.')

        if isinstance(rate, float):
            ul_min_rate = pumpcon.convert_flow_rate(rate, rate_units, 'uL/min')
            ul_min_max_rate = pumpcon.convert_flow_rate(self.settings['max_inject_rate'],
                'mL/min', 'uL/min')
            if ul_min_rate <= 0:
                errors.append('Injection rate must be > 0 uL/min')
            elif ul_min_rate > ul_min_max_rate:
                errors.append('Injection rate must <= {} uL/min'.format(
                    ul_min_max_rate))

        if trigger:
            try:
                start_delay = float(start_delay)
            except Exception:
                errors.append('Buffer start delay time must be a number.')

            if isinstance(start_delay, float):
                if start_delay < 0:
                    errors.append('Buffer start delay time must be >= 0')

        try:
            end_delay = float(end_delay)
        except Exception:
            errors.append('Buffer end delay time must be a number.')

        if isinstance(end_delay, float):
            if end_delay < 0:
                errors.append('Buffer end delay time must be >= 0')

        if len(errors) >0 and verbose:
            msg = 'The following field(s) have invalid values:'
            for err in errors:
                msg = msg + '\n- ' + err
            msg = msg + ('\n\nPlease correct these errors, then inject.')

            wx.CallAfter(wx.MessageBox, msg, 'Error in inject parameters',
                style=wx.OK|wx.ICON_ERROR)

        return (volume, rate, start_delay, end_delay, trigger, vol_units,
            rate_units, errors)

    def _inject(self, volume, rate, start_delay, end_delay, trigger, vol_units,
        rate_units, clean_needle):
        inj_cmd = ['inject_sample', [self.name, volume, rate, trigger,
            start_delay, end_delay, vol_units, rate_units], {}]

        self._send_cmd(inj_cmd, False)

        if clean_needle:
            self._send_cmd(['clean', [self.name,], {}], False)

    def home_motor(self, motor):
        self._send_cmd(['home_motor', [self.name, motor], {}], False)

    def _on_staff_ctrl_btn(self, evt):
        if self._staff_ctrl_window is None:
            self._staff_ctrl_window = StaffControlsFrame(self, self.settings, self)
            self._staff_ctrl_window.Show()
        else:
            self._staff_ctrl_window.Raise()

    def _init_device(self, settings):
        """
        Initializes the device parameters if any were provided. If enough are
        provided the device is automatically connected.
        """
        self._init_controls()

        connect_cmd = ['connect', [self.name, self.name, None],
            {'settings': settings}]

        self._send_cmd(connect_cmd, True)

        self._set_status_commands()

    def _init_controls(self):
        inj_rate = pumpcon.convert_flow_rate(self.settings['pump_rates']['sample'][1],
            'mL/min', 'uL/min')
        draw_rate = pumpcon.convert_flow_rate(self.settings['pump_rates']['sample'][0],
            'mL/min', 'uL/min')

        self.load_volume.SetValue(str(self.settings['default_load_vol']))
        self.buffer_start_delay.SetValue(str(self.settings['default_start_delay_time']))
        self.buffer_end_delay.SetValue(str(self.settings['default_end_delay_time']))
        self.inj_rate.SetValue(str(inj_rate))
        self.draw_rate.SetValue(str(draw_rate))
        self.dwell_time.SetValue(str(self.settings['load_dwell_time']))
        self.plate_types.SetStringSelection(self.settings['plate_type'])

        self._current_inj_rate = inj_rate
        self._current_draw_rate = draw_rate
        self._current_dwell_time = self.settings['load_dwell_time']
        self._current_well_plate_type = self.settings['plate_type']
        self._current_load_volume = self.settings['default_load_vol']
        self._current_buffer_start_delay = self.settings['default_start_delay_time']
        self._current_buffer_end_delay = self.settings['default_end_delay_time']

    def _set_status(self, cmd, val):
        if cmd == 'set_well_plate':
            if val != self._current_well_plate_type:
                self.plate_types.SetStringSelection(val)
                self._current_well_plate_type = val

        elif cmd == 'set_sample_draw_rate':
            if val != self._current_draw_rate:
                self.draw_rate.ChangeValue(str(val))
                self._current_draw_rate = val

        elif cmd == 'inject_sample':
            vol = val['volume']
            rate = val['rate']
            trigger = val['trigger']
            start_delay = val['start_delay']
            end_delay = val['end_delay']
            vol_units = val['vol_units']
            rate_units = val['rate_units']

            vol = pumpcon.convert_volume(vol, vol_units, 'uL')
            rate = pumpcon.convert_flow_rate(rate, rate_units, 'uL/min')

            if vol != self._current_load_volume:
                self.load_volume.ChangeValue(str(vol))
                self._current_load_volume = vol

            if rate != self._current_inj_rate:
                self.inj_rate.ChangeValue(str(rate))
                self._current_inj_rate = rate

            if trigger != self._current_trigger_on_inject:
                self.trigger_on_inject.ChangeValue(trigger)
                self._current_trigger_on_inject = trigger

            if start_delay != self._current_buffer_start_delay:
                self.buffer_start_delay.ChangeValue(str(start_delay))
                self._current_buffer_start_delay = start_delay

            if end_delay != self._current_buffer_end_delay:
                self.buffer_end_delay.ChangeValue(str(end_delay))
                self._current_buffer_end_delay = end_delay

        elif cmd == 'load_and_inject':
            vol = val['volume']
            rate = val['rate']
            trigger = val['trigger']
            start_delay = val['start_delay']
            end_delay = val['end_delay']
            vol_units = val['vol_units']
            rate_units = val['rate_units']
            row = val['row']
            column = val['column']

            well = '{}{}'.format(row, column)

            vol = pumpcon.convert_volume(vol, vol_units, 'uL')
            rate = pumpcon.convert_flow_rate(rate, rate_units, 'uL/min')

            if vol != self._current_load_volume:
                self.load_volume.ChangeValue(str(vol))
                self._current_load_volume = vol

            if rate != self._current_inj_rate:
                self.inj_rate.ChangeValue(str(rate))
                self._current_inj_rate = rate

            if trigger != self._current_trigger_on_inject:
                self.trigger_on_inject.ChangeValue(trigger)
                self._current_trigger_on_inject = trigger

            if start_delay != self._current_buffer_start_delay:
                self.buffer_start_delay.ChangeValue(str(start_delay))
                self._current_buffer_start_delay = start_delay

            if end_delay != self._current_buffer_end_delay:
                self.buffer_end_delay.ChangeValue(str(end_delay))
                self._current_buffer_end_delay = end_delay

            if well != self._selected_well:
                self._set_selected_well(well)

        elif cmd == 'load_sample' or cmd == 'load_and_move_to_inject':
            vol = val['volume']
            vol_units = val['vol_units']
            row = val['row']
            column = val['column']

            well = '{}{}'.format(row, column)

            vol = pumpcon.convert_volume(vol, vol_units, 'uL')

            if vol != self._current_load_volume:
                self.load_volume.ChangeValue(str(vol))
                self._current_load_volume = vol

            if well != self._selected_well:
                self._set_selected_well(well)

        elif cmd == 'set_sample_dwell_time':
            if val != self._current_dwell_time:
                self.dwell_time.ChangeValue(str(val))
                self._current_dwell_time = val

        elif cmd == 'get_status':
            if val != self._current_status:
                self.status.SetLabel(val)

                if self._current_status == 'Idle':
                    for btn in self._ctrl_btns:
                        btn.Disable()
                elif val == 'Idle':
                    for btn in self._ctrl_btns:
                        btn.Enable()
                elif val == 'Error':
                    msg = ('The batch mode autosampler has experienced an '
                        'error. Please contact your beamline scientist.')

                self._current_status = val

    def _set_status_commands(self):
        status_cmd = ['get_status', [self.name,], {}]
        self._update_status_cmd(status_cmd, 1)

    def metadata(self):
        metadata = OrderedDict()

        metadata['Well:'] = self._selected_well
        metadata['Loaded volume [uL]:'] = self._current_load_volume
        metadata['Draw rate [uL/min]:'] = self._current_draw_rate
        metadata['Wait time after draw [s]'] = self._current_dwell_time
        metadata['Injection rate [uL/min]:'] = self._current_inj_rate
        metadata['Delay after trigger [s]:'] = self._current_buffer_start_delay
        metadata['Delay after injection [s]:'] = self._current_buffer_end_delay
        metadata['Well plate type:'] = self._current_well_plate_type
        metadata['Trigger on inject:'] = self._current_trigger_on_inject

        return metadata

    def _get_automator_state(self):
        if self._current_status.lower() == 'idle':
            status = 'idle'
        elif self._current_status == 'Loading sample':
            status = 'load'
        elif self._current_status == 'Injecting sample':
            status = 'inject'
        elif self._current_status == 'Cleaning needle':
            status = 'clean'
        elif self._current_status == 'Changing well plate':
            status = 'change_well_plate'
        elif self._current_status == 'Moving to clean':
            status = 'move_to_clean'
        else:
            status = 'busy'

        return status

    def automator_callback(self, cmd_name, cmd_args, cmd_kwargs):
        success = True

        if cmd_name == 'status':
            state = self._get_automator_state()

        elif cmd_name == 'full_status':
            state = self._get_automator_state()

            if state != 'busy':
                state = copy.copy(self._current_status)
            else:
                state = 'Busy'

        elif cmd_name == 'abort':
            self._abort()
            state = 'idle'

        elif cmd_name == 'load_and_inject':
            volume = cmd_kwargs['volume']
            rate = cmd_kwargs['rate']
            start_delay = cmd_kwargs['start_delay']
            end_delay = cmd_kwargs['end_delay']
            trigger = cmd_kwargs['trigger']
            row = cmd_kwargs['row']
            col = cmd_kwargs['column']
            vol_units = 'uL'
            rate_units = 'uL/min'
            draw_rate = cmd_kwargs['draw_rate']
            dwell_time = cmd_kwargs['dwell_time']
            clean_needle = cmd_kwargs['clean_needle']

            self._load_and_inject(row, col, volume, rate, start_delay,
                end_delay, trigger, vol_units, rate_units, draw_rate,
                dwell_time, clean_needle)

            state = 'load'

        elif cmd_name == 'change_plate':
            wx.CallAfter(self._change_plate)
            state = 'change_well_plate'

        elif cmd_name == 'clean_needle':
            self._clean_needle()
            state = 'clean'

        elif cmd_name == 'move_to_clean':
            self._move_to_clean()
            state = 'move_to_clean'

        elif cmd_name == 'load_and_move_to_inject':
            volume = cmd_kwargs['volume']
            row = cmd_kwargs['row']
            col = cmd_kwargs['column']
            vol_units = cmd_kwargs['vol_units']
            rate_units = cmd_kwargs['rate_units']
            draw_rate = cmd_kwargs['draw_rate']
            dwell_time = cmd_kwargs['dwell_time']

            self._load_and_move_to_inject(row, col, volume, vol_units,
                rate_units, draw_rate, dwell_time)

            state = 'load'

        elif cmd_name == 'inject':
            volume = cmd_kwargs['volume']
            rate = cmd_kwargs['rate']
            start_delay = cmd_kwargs['start_delay']
            end_delay = cmd_kwargs['end_delay']
            trigger = cmd_kwargs['trigger']
            vol_units = 'uL'
            rate_units = 'uL/min'
            clean_needle = cmd_kwargs['clean_needle']

            self._inject(volume, rate, start_delay, end_delay, trigger,
                vol_units, rate_units, clean_needle)

            state = 'inject'

        return state, success

    def _on_close(self):
        """Device specific stuff goes here"""
        pass

    def on_exit(self):
        self.close()

class StaffControlsFrame(wx.Frame):

    def __init__(self, as_panel, settings, *args, **kwargs):

        wx.Frame.__init__(self, *args, **kwargs)

        self.as_panel = as_panel
        self.settings = settings

        self.Bind(wx.EVT_CLOSE, self.OnClose)

        self._create_pump_comm()
        self._create_valve_comm()

        self._create_layout()

        self.Fit()
        self.Raise()

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _create_layout(self):
        parent = wx.Panel(self)

        motor_top_sizer = self._create_motor_sizer(parent)

        pump_top_sizer = self._create_pump_sizer(parent)
        valve_top_sizer = self._create_valve_sizer(parent)

        fluidics_sizer = wx.BoxSizer(wx.HORIZONTAL)
        fluidics_sizer.Add(pump_top_sizer, flag=wx.RIGHT, border=self._FromDIP(5))
        fluidics_sizer.Add(valve_top_sizer)

        panel_sizer = wx.BoxSizer(wx.VERTICAL)
        panel_sizer.Add(motor_top_sizer, flag=wx.ALL|wx.EXPAND,
            border=self._FromDIP(5))
        panel_sizer.Add(fluidics_sizer, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM|wx.EXPAND,
            border=self.FromDIP(5))

        parent.SetSizer(panel_sizer)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(parent, flag=wx.EXPAND, proportion=1)

        self.SetSizer(top_sizer)

    def _create_motor_sizer(self, parent):
        motor_box = wx.StaticBox(parent, label='Motors and Homing')

        needle_ctrl = motorcon.EpicsMXMotorPanel(
            self.settings['device_data']['kwargs']['needle_motor']['args'][0],
            None, motor_box)
        plate_x_ctrl = motorcon.EpicsMXMotorPanel(
            self.settings['device_data']['kwargs']['plate_x_motor']['args'][0],
            None, motor_box)
        plate_z_ctrl = motorcon.EpicsMXMotorPanel(
            self.settings['device_data']['kwargs']['plate_z_motor']['args'][0],
            None, motor_box)
        coflow_y_ctrl = motorcon.EpicsMXMotorPanel(
            self.settings['device_data']['kwargs']['coflow_y_motor']['args'][0],
            None, motor_box)

        motor_sizer = wx.FlexGridSizer(cols=4, vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        motor_sizer.Add(needle_ctrl, flag=wx.EXPAND)
        motor_sizer.Add(plate_x_ctrl, flag=wx.EXPAND)
        motor_sizer.Add(plate_z_ctrl, flag=wx.EXPAND)
        motor_sizer.Add(coflow_y_ctrl, flag=wx.EXPAND)

        motor_sizer.AddGrowableCol(0)
        motor_sizer.AddGrowableCol(1)
        motor_sizer.AddGrowableCol(2)
        motor_sizer.AddGrowableCol(3)

        self._home_needle_btn = wx.Button(motor_box, label='Home Needle Y')
        self._home_plate_x_btn = wx.Button(motor_box, label='Home Plate X')
        self._home_plate_z_btn = wx.Button(motor_box, label='Home Plate Z')

        self._home_needle_btn.Bind(wx.EVT_BUTTON, self._on_home_btn)
        self._home_plate_x_btn.Bind(wx.EVT_BUTTON, self._on_home_btn)
        self._home_plate_z_btn.Bind(wx.EVT_BUTTON, self._on_home_btn)

        home_sizer = wx.BoxSizer(wx.HORIZONTAL)
        home_sizer.Add(self._home_needle_btn, flag=wx.RIGHT, border=self._FromDIP(5))
        home_sizer.Add(self._home_plate_x_btn, flag=wx.RIGHT, border=self._FromDIP(5))
        home_sizer.Add(self._home_plate_z_btn)

        motor_top_sizer = wx.StaticBoxSizer(motor_box, wx.VERTICAL)
        motor_top_sizer.Add(motor_sizer, flag=wx.ALL|wx.EXPAND, border=self._FromDIP(5))
        motor_top_sizer.Add(home_sizer, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))

        return motor_top_sizer

    def _create_pump_comm(self):
        self.pump_com_thread = pumpcon.PumpCommThread('ASPumpComm')
        self.pump_com_thread.start()

        self.pump_devices = []

        self.pump_setup_devices = [
            self.settings['device_data']['kwargs']['sample_pump'],
            self.settings['device_data']['kwargs']['clean1_pump'],
            self.settings['device_data']['kwargs']['clean2_pump'],
            self.settings['device_data']['kwargs']['clean3_pump'],
            ]

        self._pump_settings = {
            'remote'        : False,
            'remote_device' : 'pump',
            'remote_ip'     : '',
            'remote_port'   : '',
            'com_thread'    : self.pump_com_thread
            }

    def _create_pump_sizer(self, parent):
        pump_box = wx.StaticBox(parent, label='Pump Control')

        pump_sizer = wx.BoxSizer(wx.HORIZONTAL)

        for device in self.pump_setup_devices:
            dev_settings = {}
            for key, val in self._pump_settings.items():
                if key != 'com_thread':
                    dev_settings[key] = copy.deepcopy(val)
                else:
                    dev_settings[key] = val

            dev_settings['device_data'] = device
            new_device = pumpcon.PumpPanel(pump_box, wx.ID_ANY,
                dev_settings)

            pump_sizer.Add(new_device, 1, flag=wx.EXPAND|wx.ALL,
                border=self._FromDIP(3))
            self.pump_devices.append(new_device)

        pump_top_sizer = wx.StaticBoxSizer(pump_box, wx.VERTICAL)
        pump_top_sizer.Add(pump_sizer, proportion=1, flag=wx.EXPAND|wx.ALL,
            border=self._FromDIP(5))

        return pump_top_sizer

    def _create_valve_comm(self):
        self.valve_com_thread = valvecon.ValveCommThread('ASValveComm')
        self.valve_com_thread.start()

        self.valve_devices = []

        self.valve_setup_devices = [
            self.settings['device_data']['kwargs']['needle_valve'],
            ]

        self._valve_settings = {
            'remote'        : False,
            'remote_device' : 'valve',
            'remote_ip'     : '',
            'remote_port'   : '',
            'com_thread'    : self.valve_com_thread
            }

    def _create_valve_sizer(self, parent):
        valve_box = wx.StaticBox(parent, label='Valve Control')

        valve_sizer = wx.BoxSizer(wx.HORIZONTAL)

        for device in self.valve_setup_devices:
            dev_settings = {}
            for key, val in self._valve_settings.items():
                if key != 'com_thread':
                    dev_settings[key] = copy.deepcopy(val)
                else:
                    dev_settings[key] = val

            dev_settings['device_data'] = device
            new_device = valvecon.ValvePanel(valve_box, wx.ID_ANY,
                dev_settings)

            valve_sizer.Add(new_device, 1, flag=wx.EXPAND|wx.ALL,
                border=self._FromDIP(3))
            self.valve_devices.append(new_device)

        valve_top_sizer = wx.StaticBoxSizer(valve_box, wx.VERTICAL)
        valve_top_sizer.Add(valve_sizer, proportion=1, flag=wx.EXPAND|wx.ALL,
            border=self._FromDIP(5))

        return valve_top_sizer

    def _on_home_btn(self, evt):
        evt_obj = evt.GetEventObject()

        motor = None

        if evt_obj == self._home_needle_btn:
            motor = 'needle_y'
        elif evt_obj == self._home_plate_x_btn:
            motor = 'plate_x'
        elif evt_obj == self._home_plate_z_btn:
            motor = 'plate_z'

        if motor is not None:
            self.as_panel.home_motor(motor)

    def OnClose(self, evt):
        self.as_panel._staff_ctrl_window = None

        for device in self.pump_devices:
            device.close()

        self.pump_com_thread.stop()
        self.pump_com_thread.join()

        for device in self.valve_devices:
            device.close()

        self.valve_com_thread.stop()
        self.valve_com_thread.join()

        self.Destroy()


class AutosamplerFrame(utils.DeviceFrame):

    def __init__(self, name, settings, *args, **kwargs):
        """
        Initializes the device frame. Takes frame name, utils.CommManager thread
        (or subclass), the device_panel class, and args and kwargs for the wx.Frame class.
        """
        super(AutosamplerFrame, self).__init__(name, settings, AutosamplerPanel,
            *args, **kwargs)

        # Enable these to init devices on startup
        self.setup_devices = self.settings.pop('device_init', None)

        self._init_devices()


#Settings
default_autosampler_settings = {
    'device_init'           : [{'name': 'Autosampler', 'args': [], 'kwargs': {
        'needle_motor'          : {'name': 'needle_y', 'args': ['18ID_DMC_E05:35'],
                                    'kwargs': {}},
        'plate_x_motor'         : {'name': 'plate_x', 'args': ['18ID_DMC_E01:7'],
                                        'kwargs': {}},
        'plate_z_motor'         : {'name': 'plate_z', 'args': ['18ID_DMC_E01:8'],
                                        'kwargs': {}},
        'coflow_y_motor'        : {'name': 'coflow_y', 'args': ['18ID_DMC_E01:6'],
                                        'kwargs': {}},
        'needle_valve'          : {'name': 'Needle',
                                        'args':['Cheminert', 'COM10'],
                                        'kwargs': {'positions' : 6,
                                        'comm_lock': None}},
        'sample_pump'           : {'name': 'sample', 'args': ['Hamilton PSD6', 'COM8'],
                                    'kwargs': {'syringe_id': '0.05 mL, Hamilton Glass',
                                    'pump_address': '1', 'dual_syringe': 'False',
                                    'comm_lock': None,},
                                    'ctrl_args': {'flow_rate' : 100,
                                    'refill_rate' : 100, 'units': 'uL/min'}},
        'clean1_pump'           : {'name': 'water', 'args': ['KPHM100', 'COM9'],
                                    'kwargs': {'flow_cal': '319.2',
                                    'comm_lock': None},
                                    'ctrl_args': {'flow_rate': 1}},
        'clean2_pump'           : {'name': 'ethanol', 'args': ['KPHM100', 'COM11'],
                                    'kwargs': {'flow_cal': '319.2',
                                    'comm_lock': None},
                                    'ctrl_args': {'flow_rate': 1}},
        'clean3_pump'           : {'name': 'hellmanex', 'args': ['KPHM100', 'COM7'],
                                    'kwargs': {'flow_cal': '319.2',
                                    'comm_lock': None},
                                    'ctrl_args': {'flow_rate': 1}},

        }},], # Compatibility with the standard format
    'device_communication'  : 'local',
    'remote_device'         : 'autosampler',
    'remote_ip'             : '164.54.204.53',
    'remote_port'           : '5557',
    'remote'                : False,
    'volume_units'          : 'uL',
    'components'            : [],

    # 'motor_home_velocity'   : {'x': 10, 'y': 10, 'z': 10},
    # 'motor_velocity'        : {'x': 75, 'y': 75, 'z': 75}, #112
    # 'motor_acceleration'    : {'x': 500, 'y': 500, 'z': 500},
    'home_settings'         : {'plate_x': {'dir': -1, 'step': 0.1, 'pos': 0},
                                'plate_z': {'dir': 1, 'step': 0.1, 'pos': 0},
                                'needle_y': {'dir': -1, 'step': 0.01, 'pos': -2.70}}, #Direction 1/-1 for positive/negative. step is step size off limit, pos is what to set the home position as.
    'base_position'         : {'plate_x': 270.95, 'plate_z': -76.6, 'needle_y': 111.7}, # A1 well position, needle height at chiller plate top
    'clean_offsets'         : {'plate_x': 99.7, 'plate_z': -21.4, 'needle_y': -10}, # Relative to base position
    'needle_out_offset'     : 5, # mm
    'needle_in_position'    : 0,
    'plate_out_position'    : {'plate_x': -31, 'plate_z': 0}, # Relative
    'plate_load_position'   : {'plate_x': 0, 'plate_z': -75.9}, # Absolute
    'coflow_y_ref_position' : 0, # Position for coflow y motor when base position was set
    'plate_type'            : 'Thermo-Fast 96 well PCR',
    # 'plate_type'            : 'Abgene 96 well deepwell storage',
    'clean_valve_positions' : {'empty': 5, 'clean1': 1, 'clean2': 2, 'clean3': 3, 'clean4': 4, 'clean5': 5,},
    'syringe_valve_positions': {'sample': 'Output', 'clean': 'Bypass', 'purge': 'Input'},
    'clean_seq'             : [
                                ('clean1', 'dispense', 1, 0.3), #A set of (a, b, c, d) a is the valve position, b is the command, and c and d are input params for the command
                                ('clean3', 'dispense', 1, 0.3),
                                ('clean1', 'dispense', 1, 0.3),
                                ('clean2', 'dispense', 1, 0.3), # rate, volume in ml/min and ml
                                # ('clean2', 'move_y', 7, 0), #distance in mm relative to y position
                                ('clean4', 'wait', 90, 0), #wait time in s, N/A
                                ('clean5', 'move_y', 31.2, 0), #distance in mm relative to y position
                                ('clean5', 'move_x', 10, 0), #distance in mm relative to y position
                                ('clean4', 'wait', 10, 0),
                                ('clean5', 'move_x', -10, 0), #distance in mm relative to y position

                                ],
    'pump_rates'            : {'sample': (0.05, 0.1), 'buffer': (0.05, 0.1), 'purge': (1, 1)}, # (refill, infuse) rates in ml/min
    'max_inject_rate'       : 0.5,
    'max_draw_rate'         : 0.5,
    'loop_volume'           : 30, #Loop volume in uL
    'min_load_volume'       : 2.0,
    'default_load_vol'      : 29.0,
    'default_start_delay_time': 10.0,
    'default_end_delay_time': 30.0,
    'load_dwell_time'       : 45.0, #Time to wait in well after aspirating
    'inject_connect_vol'    : 0, #Volume to eject from the needle after loading before re-entering the cell, to ensure a wet-to-wet entry for the needle and prevent bubbles, uL
    'inject_connect_rate'   : 100, #Rate to eject the inject connect volume at, in uL/min
    'reserve_vol'           : 3.0, #Volume to reserve from dispensing when measuring sample, to avoid bubbles, uL
    'inline_panel'          : False,
    'load_pos_y_offset'     : 0.4,
    }


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

    #  # Local
    # com_thread = ASCommThread('ASComm')
    # com_thread.start()

    # Remote
    com_thread = None

    settings = default_autosampler_settings
    settings['components'] = ['autosampler']

    settings['com_thread'] = com_thread

    settings['remote'] = True
    settings['device_communication'] = 'remote'
    # settings['device_data'] = settings['device_init'][0]

    #Note, on linux to access serial ports must first sudo chmod 666 /dev/ttyUSB*
    # my_autosampler = Autosampler(settings)

    app = wx.App()

    # standard_paths = wx.StandardPaths.Get() #Can't do this until you start the wx app
    # info_dir = standard_paths.GetUserLocalDataDir()

    # if not os.path.exists(info_dir):
    #     os.mkdir(info_dir)
    # # if not os.path.exists(os.path.join(info_dir, 'expcon.log')):
    # #     open(os.path.join(info_dir, 'expcon.log'), 'w')
    # h2 = handlers.RotatingFileHandler(os.path.join(info_dir, 'expcon.log'), maxBytes=10e6, backupCount=5, delay=True)
    # h2.setLevel(logging.DEBUG)
    # formatter2 = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    # h2.setFormatter(formatter2)

    # logger.addHandler(h2)

    logger.debug('Setting up wx app')
    frame = AutosamplerFrame('AutosamplerFrame', settings, parent=None,
        title='Autosampler Control')
    frame.Show()
    app.MainLoop()


