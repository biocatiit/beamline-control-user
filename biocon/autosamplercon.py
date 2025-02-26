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
import epics

import motorcon
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

        if row > self.num_rows or column > self.num_columns or row < 1 or column < 1:
            raise ValueError('Invalid row or column')

        column = int(column)-1
        row = int(row)-1

        return np.array([column*(self.col_step), row*(self.row_step),
            (self.height+column*self.x_slope+row*self.y_slope)], dtype=np.float_)

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
        'height'        : 0.7, # bottom of well from chiller base plate
        'plate_height'  : 15.5, # top of plate from chiller base plate
        }
}

class Autosampler(object):

    def __init__(self, settings):

        self.settings = settings

        self.abort_event = threading.Event()
        self.abort_event.clear()
        self.running_event = threading.Event()
        self.running_event.clear()
        self.process_event = threading.Event()
        self.process_event.clear()

        self.set_clean_offsets(self.settings['clean_offsets']['plate_x'],
            self.settings['clean_offsets']['plate_z'],
            self.settings['clean_offsets']['needle_y'])

        self._init_motors()
        self._init_valves()
        self._init_pumps()

        self.set_well_plate(self.settings['plate_type'])

    def _init_motors(self):
        logger.info('Initializing autosampler motors')

        needle_args = self.settings['needle_motor']['args']
        needle_kwargs = self.settings['needle_motor']['kwargs']
        self.needle_y_motor = motorcon.EpicsMotor(self.settings['needle_motor']['name'],
            *needle_args, **needle_kwargs)

        plate_x_args = self.settings['plate_x_motor']['args']
        plate_x_kwargs = self.settings['plate_x_motor']['kwargs']
        self.plate_x_motor = motorcon.EpicsMotor(self.settings['plate_x_motor']['name'],
            *plate_x_args, **plate_x_kwargs)

        plate_z_args = self.settings['plate_z_motor']['args']
        plate_z_kwargs = self.settings['plate_z_motor']['kwargs']
        self.plate_z_motor = motorcon.EpicsMotor(self.settings['plate_z_motor']['name'],
            *plate_z_args, **plate_z_kwargs)

        coflow_args = self.settings['coflow_motor']['args']
        coflow_kwargs = self.settings['coflow_motor']['kwargs']
        self.coflow_y_motor = motorcon.EpicsMotor(self.settings['coflow_motor']['name'],
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


    def _init_valves(self):
        logger.info('Initializing autosampler valves')

        device =  self.settings['needle_valve']['args'][0]
        needle_args = self.settings['needle_valve']['args'][1:]
        needle_kwargs = self.settings['needle_valve']['kwargs']
        self.needle_valve = valvecon.known_valves[device](self.settings['needle_valve']['name'],
            *needle_args, **needle_kwargs)

    def _init_pumps(self):
        logger.info('Initializing autosampler pumps')

        device = self.settings['sample_pump']['args'][0]
        sample_args = self.settings['sample_pump']['args'][1:]
        sample_kwargs = self.settings['sample_pump']['kwargs']
        self.sample_pump = pumpcon.known_pumps[device](self.settings['sample_pump']['name'],
            *sample_args, **sample_kwargs)

        device = self.settings['clean1_pump']['args'][0]
        clean1_args = self.settings['clean1_pump']['args'][1:]
        clean1_kwargs = self.settings['clean1_pump']['kwargs']
        self.clean1_pump = pumpcon.known_pumps[device](self.settings['clean1_pump']['name'],
            *clean1_args, **clean1_kwargs)

        device = self.settings['clean2_pump']['args'][0]
        clean2_args = self.settings['clean2_pump']['args'][1:]
        clean2_kwargs = self.settings['clean2_pump']['kwargs']
        self.clean2_pump = pumpcon.known_pumps[device](self.settings['clean2_pump']['name'],
            *clean2_args, **clean2_kwargs)

        device = self.settings['clean3_pump']['args'][0]
        clean3_args = self.settings['clean3_pump']['args'][1:]
        clean3_kwargs = self.settings['clean3_pump']['kwargs']
        self.clean3_pump = pumpcon.known_pumps[device](self.settings['clean3_pump']['name'],
            *clean3_args, **clean3_kwargs)

    # def home_motors(self, motor='all'):
    #     self.running_event.set()
    #     abort = False

    #     if motor == 'all':
    #         old_velocities = [self.x_velocity, self.y_velocity, self.z_velocity]
    #         home_velocities = [self.settings['motor_home_velocity']['x'],
    #             self.settings['motor_home_velocity']['y'],
    #             self.settings['motor_home_velocity']['z']]
    #         self.set_motor_velocity(home_velocities)
    #         self.motor_z.home(False)
    #         time.sleep(0.05)
    #         self.motor_x.home(False)
    #         time.sleep(0.05)    # Necessary for the Zabers for some reason
    #         self.motor_y.home(False)
    #         time.sleep(0.05)

    #         while self.motor_x.is_moving() or self.motor_y.is_moving() or self.motor_z.is_moving():
    #             time.sleep(0.05)
    #             abort = self._check_abort()
    #             if abort:
    #                 break

    #         self.set_motor_velocity(old_velocities)

    #     elif motor == 'x':
    #         logger.info('Homing x motor')
    #         old_velocity = self.x_velocity
    #         self.set_motor_velocity(self.settings['motor_home_velocity']['x'], 'x')
    #         self.motor_x.home(False)

    #         while self.motor_x.is_moving():
    #             time.sleep(0.01)
    #             abort = self._check_abort()
    #             if abort:
    #                 break

    #         self.set_motor_velocity(old_velocity, 'x')

    #     elif motor == 'y':
    #         old_velocity = self.y_velocity
    #         self.set_motor_velocity(self.settings['motor_home_velocity']['y'], 'y')
    #         self.motor_y.home(False)

    #         while self.motor_y.is_moving():
    #             time.sleep(0.01)
    #             abort = self._check_abort()
    #             if abort:
    #                 break

    #         self.set_motor_velocity(old_velocity, 'y')

    #     elif motor == 'z':
    #         old_velocity = self.z_velocity
    #         self.set_motor_velocity(self.settings['motor_home_velocity']['z'], 'z')
    #         self.motor_z.home(False)

    #         while self.motor_z.is_moving():
    #             time.sleep(0.01)
    #             abort = self._check_abort()
    #             if abort:
    #                 break

    #         self.set_motor_velocity(old_velocity, 'z')

    #     self.running_event.clear()

    #     return not abort

    def move_motors_absolute(self, position, motor='all'):
        self.running_event.set()
        abort = False

        abort = self._check_abort()

        if not abort:
            if motor == 'all':
                plate_x_pos = position[0]
                plate_y_pos = position[1]
                needle_y_pos = position[2]

                coflow_y_pos = self.coflow_y_motor.get_position()
                offset = coflow_y_pos - self.coflow_y_ref
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
                coflow_y_pos = self.coflow_y_motor.get_position()
                offset = coflow_y_pos - self.coflow_y_ref
                position += offset

                self.needle_y_motor.move_absolute(position)
                self._sleep(0.05)

                while self.needle_y_motor.is_moving():
                    abort = self._sleep(0.02)
                    if abort:
                        break

        self.running_event.clear()

        return not abort

    def move_motors_relative(self, position, motor='all'):
        self.running_event.set()
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

        self.running_event.clear()

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
            print('here')
            self.plate_x_motor.stop()
            self.plate_z_motor.stop()
            self.needle_y_motor.stop()
            print('here2')
            self.sample_pump.stop()
            self.clean1_pump.stop()
            self.clean2_pump.stop()
            self.clean3_pump.stop()

            self.set_valve_position(self.settings['valve_positions']['sample'])

            self.abort_event.clear()

            abort = True

        else:
            abort = False

        return abort

    def stop(self):
        if self.running_event.is_set() or self.process_event.is_set():
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

        self.base_position = np.array([plate_x, plate_z, needle_y], dtype=np.float_)

        self.set_clean_position()

    def set_clean_offsets(self, clean_x, clean_z, clean_y):
        self.clean_x_off = clean_x
        self.clean_z_off = clean_z
        self.clean_y_off = clean_y

    def set_clean_position(self):
        clean_x = self.base_position[0] + self.clean_x_off
        clean_z = self.base_position[1] + self.clean_z_off
        clean_y = self.base_position[2] + self.clean_y_off

        self.clean_position = np.array([clean_x, clean_z, clean_y], dtype=np.float_)

    def set_needle_out_position(self):
        self.needle_out_position = (self.base_position[2] + self.well_plate.plate_height
            + self.settings['needle_out_offset'])

    def set_needle_in_position(self, needle_y):
        self.needle_in_position = needle_y

    def set_plate_out_position(self, plate_x, plate_z):
        self.plate_x_out = plate_x
        self.plate_z_out = plate_z

    def set_plate_load_position(self, plate_x, plate_z):
        self.plate_x_load = plate_x
        self.plate_z_load = plate_z

    def set_coflow_y_ref_position(self, coflow_ref):
        self.coflow_y_ref = coflow_ref

    def set_well_plate(self, plate_type):
        self.well_plate = WellPlate(plate_type)

        self.set_needle_out_position()

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

    def move_to_load(self, row, column):
        logger.info('Moving to load position %s%s', row, column)

        delta_position = self.well_plate.get_relative_well_position(row, column)
        well_position = self.base_position + delta_position

        success = self.move_motors_absolute(self.needle_out_position, 'needle_y')
        if success:
            self._check_abort()
            success = self.move_motors_absolute([well_position[0], well_position[1],
                self.needle_out_position])
        if success:
            self._sleep(1)
            success = self.move_motors_absolute(well_position[2], 'needle_y')

        return success

    def move_to_clean(self):
        self.process_event.set()

        logger.info('Moving to clean position')

        success = self.move_motors_absolute(self.needle_out_position, 'needle_y')
        if success:
            self._sleep(1)
            success = self.move_motors_absolute([self.clean_position[0],
                self.clean_position[1], self.needle_out_position])

        if success:
            self._sleep(1)
            success = self.move_motors_absolute(self.clean_position[2], 'needle_y')

        self.process_event.clear()

        return success

    def move_needle_out(self):
        self.process_event.set()

        logger.info('Moving needle to out position')

        success = self.move_motors_absolute(self.needle_out_position, 'needle_y')

        self.process_event.clear()

        return success

    def move_needle_in(self):
        self.process_event.set()

        logger.info('Moving needle to out position')

        self.move_plate_out()

        success = self.move_motors_absolute(self.needle_in_position, 'needle_y')

        self.process_event.clear()

        return success

    def move_plate_out(self):
        self.process_event.set()

        logger.info('Moving plate to out position')

        cur_plate_x = self.plate_x_motor.position

        if cur_plate_x != self.plate_x_load:
            success = self.move_needle_out()
        else:
            success =  True

        if success:
            self._sleep(1)
            success = self.move_motors_absolute(self.plate_x_out, 'plate_x')

        self.process_event.clear()

        return success

    def move_plate_load(self):
        self.process_event.set()

        logger.info('Moving plate to load position')

        cur_plate_x = self.plate_x_motor.position

        if cur_plate_x != self.plate_x_out:
            success = self.move_motors_absolute(self.needle_out_position, 'needle_y')
            if success:
                self._sleep(1)
        else:
            success = True

        if success:
            success = self.move_motors_absolute([self.plate_x_load,
                self.plate_z_load, self.needle_y_motor.position])

        self.process_event.clear()

        return success

    def set_valve_position(self, position):
        self.running_event.set()

        self.needle_valve.set_position(position)

        self.running_event.clear()

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
        rates = pumpcon.convert_volume(volumes, units, 'uL')
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

    def aspirate(self, volume, pump, units='uL', blocking=True):
        self.running_event.set()
        abort = False

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

            self.running_event.clear()

        else:
            abort = False

        return not abort

    def dispense(self, volume, pump, trigger=False, delay=15, units='uL',
        blocking=True):
        self.running_event.set()
        abort = False

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

        self.running_event.clear()

        return not abort

    def load_sample(self, volume, row, column, units='uL'):
        self.process_event.set()

        logger.info("Starting sample load of %s %s from %s%s", volume, units, row, column)

        self.set_valve_position(self.settings['valve_positions']['sample'])
        self.sample_pump.set_valve_position('Input')

        success = self.move_to_load(row, column)

        if success:
            rate = self.settings['pump_rates']['sample'][0]
            self.set_pump_aspirate_rates(rate, 'mL/min', 'sample')
            success = self.aspirate(volume, 'sample', units)

        if success:
            self._sleep(self.settings['load_dwell_time'])
            success = self.move_needle_out()

        self.process_event.clear()

        logger.info("Sample load finished")

        return success

    def move_to_inject(self):
        self.process_event.set()

        logger.info("Moving needle to inject position")

        self.set_valve_position(self.settings['valve_positions']['sample'])

        if self.settings['inject_connect_vol'] > 0:
            self.set_pump_dispense_rates(self.settings['inject_connect_rate'],
                'uL/min', 'sample')
            success = self.dispense(self.settings['inject_connect_vol'],
                'sample', units='uL')

        if success:
            success = self.move_needle_in()

        self.process_event.clear()

        logger.info("Needle in inject position")

        return success

    def inject_sample(self, volume, rate, trigger, delay, vol_units='uL',
        rate_units='uL/min'):
        #Flow rates ideally 100-200 uL/min?
        logger.info('Injecting sample')

        self.process_event.set()
        self.set_valve_position(self.settings['valve_positions']['sample'])

        self.set_pump_dispense_rates(rate, rate_units, 'sample')

        load_vol = pumpcon.convert_volume(volume, vol_units, 'uL')
        self.dispense(load_vol - self.settings['reserve_vol'], 'sample',
            units='uL', blocking=False)


        self.running_event.set()
        while self.sample_pump.is_moving():
            abort = self._sleep(0.02)
            if abort:
                break

        self.running_event.clear()

        self.process_event.clear()

        logger.info('Injection finished')

        return not abort

    def load_and_inject(self, volume, rate, row, column, trigger, delay,
        vol_units='uL', rate_units='uL/min'):
        initial_vol = pumpcon.convert_volume(volume, vol_units, 'uL')

        success = self.load_sample(initial_vol, row, column, 'uL')

        if success:
            success = self.move_to_inject()

            if success:
                remaining_vol = initial_vol - self.settings['inject_connect_vol']
                success = self.inject_sample(remaining_vol, rate, trigger, delay,
                'uL', rate_units)

        return success

    def clean(self):
        self.process_event.set()
        logger.info('Starting cleaning sequence')

        success = self.move_to_clean()

        self.sample_pump.set_valve_position('Output')
        rate = self.settings['pump_rates']['purge'][1]
        self.set_pump_dispense_rates(rate, 'mL/min', 'sample')
        self.sample_pump.dispense_all(blocking=False)

        if success:
            for clean_step in self.settings['clean_seq']:
                pump = clean_step[0]
                cmd = clean_step[1]
                self.set_valve_position(self.settings['valve_positions'][pump])

                if cmd == 'dispense':
                    rate = clean_step[2]
                    vol = clean_step[3]
                    self.set_pump_dispense_rates(rate, 'mL/min', pump)
                    success = self.dispense(vol, pump, units='mL')

                elif cmd == 'wait':
                    wait_time = clean_step[2]
                    abort = self._sleep(wait_time)
                    success = not abort

                if not success:
                    break

            self.set_valve_position(self.settings['valve_positions']['sample'])

        while self.sample_pump.is_moving():
            abort = self._sleep(0.02)
            if abort:
                success = not abort
                break

        self.sample_pump.set_valve_position('Input')

        self.move_needle_out()

        self.process_event.clear()

        return success

    def _sleep(self, sleep_time):
        start = time.time()

        while time.time() - start < sleep_time:
            time.sleep(0.01)
            abort = self._check_abort()
            if abort:
                break

        return abort


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
            'move_plate_load'       : self._move_plate_load,
            'set_valve_position'    : self._set_valve_position,
            'set_aspirate_rates'    : self._set_pump_aspirate_rates,
            'set_dispense_rates'    : self._set_pump_dispense_rates,
            'set_pump_volumes'      : self._set_pump_volumes,
            'pump_aspirate'         : self._pump_aspirate,
            'pump_dispense'         : self._pump_dispense,
            'load_sample'           : self._load_sample,
            'move_to_inject'        : self._move_to_inject,
            'inject_sample'         : self._inject_sample,
            'load_and_inject'       : self._load_and_inject,
            'clean'                 : self._clean,
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

    def _move_plate_load(self, name, **kwargs):
        logger.debug("%s moving plate load", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.move_plate_load(**kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s moved plate load", name)

    def _set_valve_position(self, name, val, **kwargs):
        logger.info("Setting %s valve position to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_valve_position(val, **kwargs)

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
        success = device.aspirate(val, pump, units, False, **kwargs)

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

    def _load_sample(self, name, val, row, columns, units, **kwargs):
        logger.debug("%s loading %s %s from %s%s", name, val, units, row, column)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.load_sample(val, row, column, units, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s loaded sample", name)

    def _move_to_inject(self, name, **kwargs):
        logger.debug("%s moving to inject position", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.move_to_inject(**kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s moved to inject position", name)

    def _inject_sample(self, name, val, rate, trigger, delay, vol_units,
        rate_units, **kwargs):
        logger.debug("%s injecting sample", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.inject_sample(val, rate, trigger, delay, vol_units,
            rate_units, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s injected sample", name)

    def _load_and_inject(self, name, val, rate, trow, column, trigger, delay,
        vol_units, rate_units, **kwargs):
        logger.debug("%s loading and injecting sample", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.load_and_inject(val, rate, trigger, delay, vol_units,
            rate_units, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s loaded and injected sample", name)

    def _clean(self, name, **kwargs):
        logger.debug("%s injecting sample", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.clean(val, rate, trigger, delay, vol_units,
            rate_units, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s injected sample", name)


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

        super(AutosamplerPanel, self).__init__(parent, panel_id, settings, *args, **kwargs)

    def _create_layout(self):
        """Creates the layout for the panel."""
        parent = self

        self.well_bmp = utils.load_DIP_bitmap('./resources/icons8-circled-thin-42.png',
            wx.BITMAP_TYPE_PNG, False)['light']
        self.selected_well_bmp = utils.load_DIP_bitmap('./resources/sel_icons8-circled-thin-42.png',
            wx.BITMAP_TYPE_PNG, False)['light']

        basic_ctrl_sizer = self._make_basic_controls(parent)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(basic_ctrl_sizer)

        self.SetSizer(top_sizer)

    def _make_basic_controls(self, parent):
        as_box = wx.StaticBox(parent, label="Autosampler controls")

        self.sample_well = wx.StaticText(as_box, size=self._FromDIP((40,-1)),
                style=wx.ST_NO_AUTORESIZE)

        ctrl_sizer = wx.FlexGridSizer(cols=2, hgap=self._FromDIP(5),
            vgap=self._FromDIP(5))
        ctrl_sizer.Add(wx.StaticText(as_box, label='Sample Well:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        ctrl_sizer.Add(self.sample_well, flag=wx.ALIGN_CENTER_VERTICAL)

        col_labels = ['{}'.format(col) for col in range(1,13)]
        row_labels = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
        row_idx = -1
        col_idx = 0
        wells = ['{}{}'.format(row, col) for row in row_labels for col in col_labels]
        self.well_ids_96 = {well :wx.NewIdRef() for well in wells}
        self.reverse_well_ids_96 = {wid: well for (well, wid) in self.well_ids_96.items()}


        well_box = wx.StaticBox(as_box, label='Well Plate')

        self.well_plate_96 = wx.FlexGridSizer(cols=13, rows=9, hgap=self._FromDIP(0),
            vgap=self._FromDIP(0))
        self.well_plate_96.AddSpacer(1)

        for col in col_labels:
            self.well_plate_96.Add(wx.StaticText(well_box, label=col),
                flag=wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_CENTER_VERTICAL)

        for i in range(104):
            if i % 13 == 0:
                row_idx += 1
                col_idx = 0
                row = row_labels[row_idx]

                self.well_plate_96.Add(wx.StaticText(well_box, label=row),
                    flag=wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_CENTER_VERTICAL)

            else:
                row = row_labels[row_idx]
                col = col_labels[col_idx]
                col_idx += 1

                btn_id = self.well_ids_96['{}{}'.format(row, col)]

                well_btn = wx.BitmapButton(well_box, btn_id, self.well_bmp,
                    style=wx.NO_BORDER)
                well_btn.SetToolTip('{}{}'.format(row, col))
                well_btn.Bind(wx.EVT_BUTTON, self._on_well_button)
                self.well_plate_96.Add(well_btn,
                    flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)

        well_sizer = wx.StaticBoxSizer(well_box, wx.HORIZONTAL)
        well_sizer.Add(self.well_plate_96,flag=wx.ALL, border=self._FromDIP(5))

        top_sizer = wx.StaticBoxSizer(as_box, wx.HORIZONTAL)
        top_sizer.Add(ctrl_sizer)
        top_sizer.Add(well_sizer, flag=wx.ALL, border=self._FromDIP(5))

        return top_sizer

    def _make_advanced_controls(self, parent):
        box = wx.StaticBox(parent, label="Plate controls")

        plate_types = wx.Choice(box, choices=known_well_plates.keys())
        plate_types.Bind(self._on_change_plate_type)

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
        ctrl = self.FindWindowById(ctrl_id)

        print(ctrl.GetSize())
        print(ctrl.GetPosition())
        print(ctrl.GetScreenPosition())

        if self._selected_well in self.well_ids_96:
            old_ctrl = wx.FindWindowById(self.well_ids_96[self._selected_well])
            old_ctrl.SetBitmap(self.well_bmp)

        ctrl.SetBitmap(self.selected_well_bmp)
        self.Layout()
        self.Refresh()
        self._selected_well = well

        self.sample_well.SetLabel(self._selected_well)

        print(ctrl.GetSize())
        print(ctrl.GetPosition())
        print(ctrl.GetScreenPosition())

    def _init_device(self, settings):
        """
        Initializes the device parameters if any were provided. If enough are
        provided the device is automatically connected.
        """
        self._init_controls()

        # device_data = settings['device_data']
        # args = device_data['args']
        # kwargs = device_data['kwargs']

        # args.insert(0, self.name)

        # connect_cmd = ['connect', [settings,], {}]

        # self._send_cmd(connect_cmd, True)

    def _init_controls(self):
        pass

    def _set_status(self, cmd, val):
        # if cmd == 'set_int_time':
        #     if str(val) != self.int_time.GetValue():
        #         self.int_time.SafeChangeValue(str(val))
        #         self._current_int_time = int_time
        pass


    def _set_status_commands(self):
        # settings_cmd = ['get_spec_settings', [self.name], {}]

        # self._update_status_cmd(settings_cmd, 60)

        # busy_cmd = ['get_busy', [self.name,], {}]

        # self._update_status_cmd(busy_cmd, 1)
        pass

    def metadata(self):
        metadata = OrderedDict()

        return metadata

    def _on_close(self):
        """Device specific stuff goes here"""
        pass

    def on_exit(self):
        self.close()

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
    'device_init'           : [{'name': 'Autosampler', 'args': [], 'kwargs': {}},], # Compatibility with the standard format
    'device_communication'  : 'local',
    # 'remote_pump_ip'        : '164.54.204.37',
    # 'remote_pump_port'      : '5556',
    # 'remote_fm_ip'          : '164.54.204.37',
    # 'remote_fm_port'        : '5557',
    'volume_units'          : 'uL',
    'components'            : [],
    'needle_motor'          : {'name': 'needle_y', 'args': ['18ID_DMC_E05:33'],
                                    'kwargs': {}},
    'plate_x_motor'         : {'name': 'plate_x', 'args': ['18ID_DMC_E05:37'],
                                    'kwargs': {}},
    'plate_z_motor'         : {'name': 'plate_z', 'args': ['18ID_DMC_E05:34'],
                                    'kwargs': {}},
    'coflow_y_motor'        : {'name': 'coflow_y', 'args': ['18ID_DMC_E03_23'],
                                    'kwargs': {}},
    'needle_valve'          : {'name': 'Needle',
                                    'args':['Cheminert', 'COM11'],
                                    'kwargs': {'positions' : 6}},
    'sample_pump'           : {'name': 'sample', 'args': ['Hamilton PSD6', 'COM12'],
                                'kwargs': {'syringe_id': '0.1 mL, Hamilton Glass',
                                'pump_address': '1', 'dual_syringe': 'False',
                                'diameter': 1.46, 'max_volume': 0.1,
                                'max_rate': 1},},
    'clean1_pump'           : {'name': 'water', 'args': ['KPHM100', 'COM10'],
                                'kwargs': {'flow_cal': '319.2',},
                                'ctrl_args': {'flow_rate': 1}},
    'clean2_pump'           : {'name': 'ethanol', 'args': ['KPHM100', 'COM8'],
                                'kwargs': {'flow_cal': '319.2',},
                                'ctrl_args': {'flow_rate': 1}},
    'clean3_pump'           : {'name': 'hellmanex', 'args': ['KPHM100', 'COM9'],
                                'kwargs': {'flow_cal': '319.2',},
                                'ctrl_args': {'flow_rate': 1}},
    # 'motor_home_velocity'   : {'x': 10, 'y': 10, 'z': 10},
    # 'motor_velocity'        : {'x': 75, 'y': 75, 'z': 75}, #112
    # 'motor_acceleration'    : {'x': 500, 'y': 500, 'z': 500},
    'base_position'         : {'plate_x': 270.9, 'plate_z': -82.1, 'needle_y': 102.685}, # A1 well position, needle height at chiller plate top
    'clean_offsets'         : {'plate_x': 96, 'plate_z': -17, 'needle_y': 0.7}, # Relative to base position
    'needle_out_offset'     : 5, # mm
    'needle_in_position'    : 0,
    'plate_out_position'    : {'plate_x': 241.4, 'plate_z': -82.1},
    'plate_load_position'   : {'plate_x': 0, 'plate_z': -82.1},
    'coflow_y_ref_position' : 0, # Position for coflow y motor when base position was set
    'plate_type'            : 'Thermo-Fast 96 well PCR',
    # 'plate_type'            : 'Abgene 96 well deepwell storage',
    'valve_positions'       : {'sample': 5, 'clean1': 1, 'clean2': 2, 'clean3': 3, 'clean4': 4},
    'clean_seq'             : [('clean1', 'dispense', 5, 1), #A set of (a, b, c, d) a is the valve position, b is the command, and c and d are input params for the command
                                ('clean3', 'dispense', 5, 1),
                                ('clean1', 'dispense', 5, 1),
                                ('clean2', 'dispense', 5, 1), # rate, volume in ml/min and ml
                                ('clean4', 'wait', 60, 0),], #wait time in s, N/A
    'pump_rates'            : {'sample': (0.3, 0.1), 'buffer': (0.1, 0.1), 'purge': (1, 1)}, # (refill, infuse) rates in ml/min
    'loop_volume'           : 0.1,
    'load_dwell_time'       : 3, #Time to wait in well after aspirating
    'inject_connect_vol'    : 0, #Volume to eject from the needle after loading before re-entering the cell, to ensure a wet-to-wet entry for the needle and prevent bubbles, uL
    'inject_connect_rate'   : 100, #Rate to eject the inject connect volume at, in uL/min
    'reserve_vol'           : 1, #Volume to reserve from dispensing when measuring sample, to avoid bubbles, uL
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

     # Local
    com_thread = ASCommThread('ASComm')
    com_thread.start()

    # # Remote
    # com_thread = None

    settings = default_autosampler_settings
    settings['components'] = ['uv']

    settings['com_thread'] = com_thread

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
    frame = AutosamplerFrame('AutosamplerFrame', settings, parent=None, title='Autosampler Control')
    frame.Show()
    app.MainLoop()


