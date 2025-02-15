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

class WellPlate(object):

    def __init__(self, plate_type):

        self.plate_params = {
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

        self.set_base_position(self.settings['base_position']['plate_x'],
            self.settings['base_position']['plate_z'],
            self.settings['base_position']['needle_y'])

        self.set_needle_in_position(self.settings['needle_in_position'])

        self.set_plate_out_position(self.settings['plate_out_position']['plate_x'],
            self.settings['plate_out_position']['plate_z'])

        self.set_plate_load_position(self.settings['plate_load_position']['plate_x'],
            self.settings['plate_load_position']['plate_z'])


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
                self.plate_x_motor.move_absolute(position[0])
                self.plate_z_motor.move_absolute(position[1])
                self.needle_y_motor.move_absolute(position[2])
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

                while self.plate_x_motor.is_moving() or self.needle_y_motor.is_moving() or self.plate_z_motor.is_moving():
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
        logger.info('Moving to load position for row: %s column: %s', row,
            column)

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

        logger.info('Moving to out position')

        success = self.move_motors_absolute(self.needle_out_position, 'needle_y')

        self.process_event.clear()

        return success

    def move_needle_in(self):
        self.process_event.set()

        logger.info('Moving to out position')

        self.move_plate_out()

        success = self.move_motors_absolute(self.needle_in_position, 'needle_y')

        self.process_event.clear()

        return success

    def move_plate_out(self):
        self.process_event.set()

        logger.info('Moving to out position')

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

        logger.info('Moving to load position')

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

    def set_pump_volumes(self, volumes, pump='all'):
        if pump == 'all':
            self.sample_pump.volume = volumes[0]
            self.clean1_pump.volume = volumes[1]
            self.clean2_pump.volume = volumes[2]
            self.clean3_pump.volume = volumes[3]
        elif pump == 'sample':
            self.sample_pump.volume == volumes
        elif pump == 'clean1':
            self.clean1_pump.volume = volumes
        elif pump == 'clean2':
            self.clean2_pump.volume = volumes
        elif pump == 'clean3':
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

        initial_volume = selected_pump.volume

        selected_pump.aspirate(volume, units=units)

        if blocking:
            while selected_pump.is_moving():
                abort = self._sleep(0.05)
                if abort:
                    break

            self.running_event.clear()

            final_volume = selected_pump.volume

            if round(initial_volume + volume, 4) != round(final_volume, 4):
                logger.error('Pump %s failed to aspirate requested '
                    'volume! Volume requested: %f, volume '
                    'aspirated: %f', pump, volume,
                    final_volume-initial_volume)
                raise Exception('Pump aspirate failed!')

        else:
            abort = False

        return not abort

    def dispense(self, volume, pump, units='uL', blocking=True):
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

        # initial_volume = selected_pump.volume

        selected_pump.dispense(volume, units=units)

        if blocking:
            while selected_pump.is_moving():
                abort = self._sleep(0.05)

            # final_volume = selected_pump.volume

            # if round(initial_volume - final_volume, 4) != round(volume, 4):
            #     logger.error('Pump %s failed to dispense requested '
            #         'volume! Volume requested: %f, volume dispensed: %f',
            #         pump, volume, initial_volume - final_volume)
            #     raise Exception('Pump dispense failed!')

        else:
            abort = False

        self.running_event.clear()

        return not abort

    def load_sample(self, volume, row, column, units='uL'):
        self.process_event.set()

        logger.info("Starting sample load")

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

        self.set_pump_dispense_rates(self.settings['inject_connect_rate'], 'uL/min', 'sample')
        success = self.dispense(self.settings['inject_connect_vol'], 'sample', units='uL')

        if success:
            success = self.move_needle_in()

        self.process_event.clear()

        logger.info("Needle in inject position")

        return success

    def inject_sample(self, volume, rate, vol_units='uL', rate_units='uL/min'):
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

    def load_and_inject(self, volume, rate, row, column, vol_units='uL',
        rate_units='uL/min'):
        initial_vol = pumpcon.convert_volume(volume, vol_units, 'uL')

        success = self.load_sample(initial_vol, row, column, 'uL')

        if success:
            success = self.move_to_inject()

            if success:
                remaining_vol = initial_vol - self.settings['inject_connect_vol']
                success = self.inject_sample(remaining_vol, rate, 'uL', rate_units)

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



# class Old_Autosampler(object):

#     def __init__(self, settings):

#         self.settings = settings

#         self.abort_event = threading.Event()
#         self.abort_event.clear()
#         self.running_event = threading.Event()
#         self.running_event.clear()
#         self.process_event = threading.Event()
#         self.process_event.clear()

#         self._init_motors()
#         self._init_valves()
#         self._init_pumps()

#         self.set_well_plate(self.settings['plate_type'])
#         self.set_chiller_top_on(self.settings['chiller_top_on'])
#         self.set_clean_sequence(self.settings['clean_buffer_seq'], 'buffer')
#         self.set_clean_sequence(self.settings['clean_sample_seq'], 'sample')

#     def _init_motors(self):
#         logger.info('Initializing autosampler motors')

#         if self.settings['motors'] == 'zaber':
#             self.zaber_ports = {}

#             for motor_name, motor_settings in self.settings['zaber_motors'].items():
#                 port = motor_settings[0]
#                 number = motor_settings[1]
#                 travel = motor_settings[2]

#                 if port not in self.zaber_ports:
#                     binary_serial = zaber.BinarySerial(str(port))
#                     binary_serial.close()
#                     lock = threading.Lock()
#                     self.zaber_ports[port] = (binary_serial, lock)

#                     binary_serial.lock.acquire()

#                     try:
#                         binary_serial.open()
#                         while binary_serial.can_read():
#                             reply = binary_serial.read()
#                         # Device number 0, command number 2, renumber.
#                         command = zaber.BinaryCommand(0, 2)
#                         binary_serial.write(command)

#                         time.sleep(5)

#                         while binary_serial.can_read():
#                             reply = binary_serial.read()
#                             if motorcon.ZaberMotor.check_command_succeeded(reply):
#                                 logger.debug("Zaber device renumbered")
#                             else:
#                                 logger.error("Zaber device renumbering failed")

#                     except Exception:
#                         raise
#                     finally:
#                         binary_serial.close()

#                     binary_serial.lock.release()

#                 else:
#                     binary_serial, lock = self.zaber_ports[port]

#                 if motor_name == 'x':
#                     self.motor_x = motorcon.ZaberMotor(port, motor_name,
#                         binary_serial, lock, number, travel)

#                 elif motor_name == 'y':
#                     self.motor_y = motorcon.ZaberMotor(port, motor_name,
#                         binary_serial, lock, number, travel)

#                 elif motor_name == 'z':
#                     self.motor_z = motorcon.ZaberMotor(port, motor_name,
#                         binary_serial, lock, number, travel)

#         velocities = [self.settings['motor_velocity']['x'],
#             self.settings['motor_velocity']['y'],
#             self.settings['motor_velocity']['z']]

#         self.set_motor_velocity(velocities)

#         accels = [self.settings['motor_acceleration']['x'],
#             self.settings['motor_acceleration']['y'],
#             self.settings['motor_acceleration']['z']]

#         self.set_motor_acceleration(accels)

#         self.set_base_position(self.settings['base_position']['x'],
#             self.settings['base_position']['y'], self.settings['base_position']['z'])

#         self.set_clean_position(self.settings['clean_position']['x'],
#             self.settings['clean_position']['y'], self.settings['clean_position']['z'])

#         self.set_out_position(self.settings['out_position']['x'],
#             self.settings['out_position']['y'], self.settings['out_position']['z'])

#     def _init_valves(self):
#         logger.info('Initializing autosampler valves')
#         if self.settings['valves'] == 'rheodyne':
#             self.injection_valve = valvecon.RheodyneValve(
#                 self.settings['rheodyne_valves']['injection'][0], 'injection',
#                 self.settings['rheodyne_valves']['injection'][1])

#             self.sample_valve = valvecon.RheodyneValve(
#                 self.settings['rheodyne_valves']['sample'][0], 'sample',
#                 self.settings['rheodyne_valves']['sample'][1])

#             self.buffer_valve = valvecon.RheodyneValve(
#                 self.settings['rheodyne_valves']['buffer'][0], 'buffer',
#                 self.settings['rheodyne_valves']['buffer'][1])

#             self.bypass_valve = valvecon.RheodyneValve(
#                 self.settings['rheodyne_valves']['bypass'][0], 'bypass',
#                 self.settings['rheodyne_valves']['bypass'][1])

#             self.autosampler_valve = valvecon.RheodyneValve(
#                 self.settings['rheodyne_valves']['autosampler'][0], 'autosampler',
#                 self.settings['rheodyne_valves']['autosampler'][1])

#         valve_pos = [self.settings['valve_positions']['injection'],
#             self.settings['valve_positions']['sample'],
#             self.settings['valve_positions']['buffer'],
#             self.settings['valve_positions']['bypass'],
#             self.settings['valve_positions']['autosampler']]

#         self.set_valve_positions(valve_pos)

#     def _init_pumps(self):
#         logger.info('Initializing autosampler pumps')

#         self.known_syringes = {'30 mL, EXEL': {'diameter': 23.5, 'max_volume': 30,
#             'max_rate': 70},
#             '3 mL, Medline P.C.': {'diameter': 9.1, 'max_volume': 3,
#             'max_rate': 11},
#             '6 mL, Medline P.C.': {'diameter': 12.8, 'max_volume': 6,
#             'max_rate': 23},
#             '10 mL, Medline P.C.': {'diameter': 16.4, 'max_volume': 10,
#             'max_rate': 31},
#             '20 mL, Medline P.C.': {'diameter': 20.4, 'max_volume': 20,
#             'max_rate': 55},
#             '0.25 mL, Hamilton Glass': {'diameter': 2.30, 'max_volume': 0.25,
#             'max_rate': 11},
#             '0.5 mL, Hamilton Glass': {'diameter': 3.26, 'max_volume': 0.5,
#             'max_rate': 11},
#             '1.0 mL, Hamilton Glass': {'diameter': 4.61, 'max_volume': 1.0,
#             'max_rate': 11},
#             }

#         if self.settings['pumps'] == 'harvard':
#             self.harvard_ports = {}

#             for pump_name, pump_settings in self.settings['harvard_pumps'].items():
#                 port = pump_settings[0]
#                 number = pump_settings[1]
#                 syringe = pump_settings[2]

#                 if port not in self.zaber_ports:
#                     lock = threading.Lock()
#                     self.harvard_ports[port] = lock
#                 else:
#                     lock = self.harvard_ports[port]

#                 if syringe in self.known_syringes:
#                     diam = self.known_syringes[syringe]['diameter']
#                     max_vol = self.known_syringes[syringe]['max_volume']
#                     max_rate = self.known_syringes[syringe]['max_rate']
#                 else:
#                     logger.error(("Unknown syringe {} specified. No syringe "
#                         "set for pump.".format(syringe)))
#                     diam = 0
#                     max_vol = 0
#                     max_rate = 0

#                 if pump_name == 'sample':
#                     self.pump_sample = pumpcon.PHD4400Pump(port, pump_name,
#                         number, diam, max_vol, max_rate, syringe, lock)

#                 elif pump_name == 'buffer':
#                     self.pump_buffer = pumpcon.PHD4400Pump(port, pump_name,
#                         number, diam, max_vol, max_rate, syringe, lock)

#         self.set_pump_aspirate_rates([self.settings['pump_rates']['sample'][0],
#             self.settings['pump_rates']['buffer'][0]])

#         self.set_pump_dispense_rates([self.settings['pump_rates']['sample'][1],
#             self.settings['pump_rates']['buffer'][1]])

#         self.set_pump_offset_volumes([self.settings['swept_volumes']['sample'],
#             self.settings['swept_volumes']['buffer']])

#         self.set_loop_volume(self.settings['loop_volume'])
#         self.set_sample_overdraw(self.settings['sample_overdraw'])

#         self.buffer_flush_volume = self.settings['buffer_flush_volume']
#         self.buffer_reserve_volume = self.settings['buffer_reserve_volume']
#         self.buffer_delay_volume = self.settings['buffer_delay_volume']

#     def home_motors(self, motor='all'):
#         self.running_event.set()
#         abort = False

#         if motor == 'all':
#             old_velocities = [self.x_velocity, self.y_velocity, self.z_velocity]
#             home_velocities = [self.settings['motor_home_velocity']['x'],
#                 self.settings['motor_home_velocity']['y'],
#                 self.settings['motor_home_velocity']['z']]
#             self.set_motor_velocity(home_velocities)
#             self.motor_z.home(False)
#             time.sleep(0.05)
#             self.motor_x.home(False)
#             time.sleep(0.05)    # Necessary for the Zabers for some reason
#             self.motor_y.home(False)
#             time.sleep(0.05)

#             while self.motor_x.is_moving() or self.motor_y.is_moving() or self.motor_z.is_moving():
#                 time.sleep(0.05)
#                 abort = self._check_abort()
#                 if abort:
#                     break

#             self.set_motor_velocity(old_velocities)

#         elif motor == 'x':
#             logger.info('Homing x motor')
#             old_velocity = self.x_velocity
#             self.set_motor_velocity(self.settings['motor_home_velocity']['x'], 'x')
#             self.motor_x.home(False)

#             while self.motor_x.is_moving():
#                 time.sleep(0.01)
#                 abort = self._check_abort()
#                 if abort:
#                     break

#             self.set_motor_velocity(old_velocity, 'x')

#         elif motor == 'y':
#             old_velocity = self.y_velocity
#             self.set_motor_velocity(self.settings['motor_home_velocity']['y'], 'y')
#             self.motor_y.home(False)

#             while self.motor_y.is_moving():
#                 time.sleep(0.01)
#                 abort = self._check_abort()
#                 if abort:
#                     break

#             self.set_motor_velocity(old_velocity, 'y')

#         elif motor == 'z':
#             old_velocity = self.z_velocity
#             self.set_motor_velocity(self.settings['motor_home_velocity']['z'], 'z')
#             self.motor_z.home(False)

#             while self.motor_z.is_moving():
#                 time.sleep(0.01)
#                 abort = self._check_abort()
#                 if abort:
#                     break

#             self.set_motor_velocity(old_velocity, 'z')

#         self.running_event.clear()

#         return not abort

#     def move_motors_absolute(self, position, motor='all'):
#         self.running_event.set()
#         abort = False

#         abort = self._check_abort()

#         if not abort:
#             if motor == 'all':
#                 self.motor_z.move_absolute(position[2], blocking=False)
#                 time.sleep(0.05)
#                 self.motor_x.move_absolute(position[0], blocking=False)
#                 time.sleep(0.05)
#                 self.motor_y.move_absolute(position[1], blocking=False)
#                 time.sleep(0.05)

#                 while self.motor_x.is_moving() or self.motor_y.is_moving() or self.motor_z.is_moving():
#                     time.sleep(0.05)
#                     abort = self._check_abort()
#                     if abort:
#                         break

#             elif motor == 'x':
#                 self.motor_x.move_absolute(position, False)
#                 time.sleep(0.05)

#                 while self.motor_x.is_moving():
#                     time.sleep(0.01)
#                     abort = self._check_abort()
#                     if abort:
#                         break

#             elif motor == 'y':
#                 self.motor_y.move_absolute(position, False)
#                 time.sleep(0.05)

#                 while self.motor_y.is_moving():
#                     time.sleep(0.01)
#                     abort = self._check_abort()
#                     if abort:
#                         break

#             elif motor == 'z':
#                 self.motor_z.move_absolute(position, False)
#                 time.sleep(0.05)

#                 while self.motor_z.is_moving():
#                     time.sleep(0.01)
#                     abort = self._check_abort()
#                     if abort:
#                         break

#         self.running_event.clear()

#         return not abort

#     def move_motors_relative(self, position, motor='all'):
#         self.running_event.set()
#         abort = False

#         abort = self._check_abort()

#         if not abort:
#             if motor == 'all':
#                 self.motor_z.move_relative(position[2], blocking=False)
#                 time.sleep(0.05)
#                 self.motor_x.move_relative(position[0], blocking=False)
#                 time.sleep(0.05)
#                 self.motor_y.move_relative(position[1], blocking=False)
#                 time.sleep(0.05)

#                 while self.motor_x.is_moving() or self.motor_y.is_moving() or self.motor_z.is_moving():
#                     time.sleep(0.05)
#                     abort = self._check_abort()
#                     if abort:
#                         break

#             elif motor == 'x':
#                 self.motor_x.move_relative(position, False)
#                 time.sleep(0.05)

#                 while self.motor_x.is_moving():
#                     time.sleep(0.01)
#                     abort = self._check_abort()
#                     if abort:
#                         break

#             elif motor == 'y':
#                 self.motor_y.move_relative(position, False)
#                 time.sleep(0.05)

#                 while self.motor_y.is_moving():
#                     time.sleep(0.01)
#                     abort = self._check_abort()
#                     if abort:
#                         break

#             elif motor == 'z':
#                 self.motor_z.move_relative(position, False)
#                 time.sleep(0.05)

#                 while self.motor_z.is_moving():
#                     time.sleep(0.01)
#                     abort = self._check_abort()
#                     if abort:
#                         break

#         self.running_event.clear()

#         return not abort

#     def set_motor_velocity(self, velocity, motor='all'):
#         if motor == 'all':
#             self.x_velocity = float(velocity[0])
#             self.y_velocity = float(velocity[1])
#             self.z_velocity = float(velocity[2])

#             self.motor_x.set_velocity(self.x_velocity)
#             self.motor_y.set_velocity(self.y_velocity)
#             self.motor_z.set_velocity(self.z_velocity)

#         elif motor == 'x':
#             self.x_velocity = float(velocity)
#             self.motor_x.set_velocity(self.x_velocity)

#         elif motor == 'y':
#             self.y_velocity = float(velocity)
#             self.motor_y.set_velocity(self.y_velocity)

#         elif motor == 'z':
#             self.z_velocity = float(velocity)
#             self.motor_z.set_velocity(self.z_velocity)


#     def set_motor_acceleration(self, accel, motor='all'):
#         if motor == 'all':
#             self.x_accel = float(accel[0])
#             self.y_accel = float(accel[1])
#             self.z_accel = float(accel[2])

#             self.motor_x.set_acceleration(self.x_accel)
#             self.motor_y.set_acceleration(self.y_accel)
#             self.motor_z.set_acceleration(self.z_accel)

#         elif motor == 'x':
#             self.x_accel = float(accel)
#             self.motor_x.set_acceleration(self.x_accel)

#         elif motor == 'y':
#             self.y_accel = float(accel)
#             self.motor_y.set_acceleration(self.y_accel)

#         elif motor == 'z':
#             self.z_accel = float(accel)
#             self.motor_z.set_acceleration(self.z_accel)

#     def _check_abort(self):
#         if self.abort_event.is_set():
#             self.motor_x.stop()
#             self.motor_y.stop()
#             self.motor_z.stop()

#             self.pump_sample.stop()
#             self.pump_buffer.stop()

#             self.set_valve_positions(1, 'injection')
#             self.set_valve_positions(1, 'bypass')
#             self.set_valve_position(2, 'sample')
#             self.set_valve_position(1, 'buffer')
#             self.set_valve_position(1, 'autosampler')

#             self.abort_event.clear()

#             abort = True

#         else:
#             abort = False

#         return abort

#     def stop(self):
#         if self.running_event.is_set() or self.process_event.is_set():
#             self.abort_event.set()

#     def set_base_position(self, x, y, z):
#         """
#         This sets the base position from which relative well positions are
#         calculated based on the definitions in the WellPlate class.

#         This should be the position with the needle centered in the A1 well
#         and tip height at the top of the lower chiller plate.
#         """

#         self.base_position = np.array([x, y, z], dtype=np.float_)

#     def set_clean_position(self, x, y, z):
#         self.clean_position = np.array([x, y, z], dtype=np.float_)

#     def set_out_position(self, x, y, z):
#         self.out_position = np.array([x, y, z], dtype=np.float_)

#     def set_well_plate(self, plate_type):
#         self.well_plate = WellPlate(plate_type)

#     def set_well_volume(self, volume, row, column):
#         """
#         Expects column and row to be 1 indexed, like on a plate!
#         """
#         self.well_plate.set_well_volume(volume, row, column)

#     def get_well_volume(self, row, column):
#         """
#         Expects column and row to be 1 indexed, like on a plate!
#         """
#         volume = self.well_plate.get_well_volume(row, column)

#         return volume

#     def set_all_well_volumes(self, volume):

#         self.well_plate.set_all_well_volumes(volume)

#     def get_all_well_volumes(self):
#         well_volumes = self.well_plate.get_all_well_volumes()

#         return well_volumes

#     def set_chiller_top_on(self, status):
#         self.chiller_top_on = status

#     def move_to_load(self, row, column):
#         if self.chiller_top_on:
#             if isinstance(row, string_types):
#                 row = ord(row.lower()) - 96

#             if row%2 != 0:
#                 raise ValueError('Cannot access odd rows with chiller top plate on!')

#         logger.info('Moving to load position for row: %s column: %s', row,
#             column)

#         delta_position = self.well_plate.get_relative_well_position(row, column)
#         well_position = self.base_position + delta_position

#         success = self.move_motors_absolute(self.out_position[2], 'z')
#         if success:
#             self._check_abort()
#             success = self.move_motors_absolute([well_position[0], well_position[1],
#                 self.out_position[2]])
#         if success:
#             self._sleep(2)
#             success = self.move_motors_absolute(well_position[2], 'z')

#         return success

#     def move_to_clean(self):
#         self.process_event.set()

#         logger.info('Moving to clean position')

#         success = self.move_motors_absolute(self.out_position[2], 'z')
#         if success:
#             self._sleep(2)
#             success = self.move_motors_absolute([self.clean_position[0],
#                 self.clean_position[1], self.out_position[2]])
#         if success:
#             self._sleep(2)
#             success = self.move_motors_absolute(self.clean_position[2], 'z')

#         self.process_event.clear()

#         return success

#     def move_to_out(self):
#         self.process_event.set()

#         logger.info('Moving to out position')

#         success = self.move_motors_absolute(self.out_position[2], 'z')
#         if success:
#             self._sleep(2)
#             success = self.move_motors_absolute(self.out_position)

#         self.process_event.clear()

#         return success

#     def move_to_z_out(self):
#         self.process_event.set()

#         logger.info('Moving to z out position')

#         success = self.move_motors_absolute(self.out_position[2], 'z')

#         self.process_event.clear()

#         return success

#     def set_valve_positions(self, positions, valve='all'):
#         self.running_event.set()

#         if valve == 'all':
#             logger.info('Setting injection valve position to %s',
#                 positions[0])
#             logger.info('Setting sample valve position to %s',
#                 positions[1])
#             logger.info('Setting buffer valve position to %s',
#                 positions[2])
#             logger.info('Setting bypass valve position to %s',
#                 positions[3])
#             logger.info('Setting autosampler valve position to %s',
#                 positions[4])
#             self.injectionv_position = int(positions[0])
#             self.samplev_position = int(positions[1])
#             self.bufferv_position = int(positions[2])
#             self.bypassv_position = int(positions[3])
#             self.autosamplerv_position = int(positions[4])

#             self.injection_valve.set_position(self.injectionv_position)
#             self.sample_valve.set_position(self.samplev_position)
#             self.buffer_valve.set_position(self.bufferv_position)
#             self.bypass_valve.set_position(self.bypassv_position)
#             self.autosampler_valve.set_position(self.autosamplerv_position)

#         elif valve == 'injection':
#             logger.info('Setting injection valve position to %s',
#                 positions)
#             self.injectionv_position = int(positions)
#             self.injection_valve.set_position(self.injectionv_position)

#         elif valve == 'sample':
#             logger.info('Setting sample valve position to %s',
#                 positions)
#             self.samplev_position = int(positions)
#             self.sample_valve.set_position(self.samplev_position)

#         elif valve == 'buffer':
#             logger.info('Setting buffer valve position to %s',
#                 positions)
#             self.bufferv_position = int(positions)
#             self.buffer_valve.set_position(self.bufferv_position)

#         elif valve == 'bypass':
#             logger.info('Setting bypass valve position to %s',
#                 positions)
#             self.bypassv_position = int(positions)
#             self.bypass_valve.set_position(self.bypassv_position)

#         elif valve == 'autosampler':
#             logger.info('Setting autosampler valve position to %s',
#                 positions)
#             self.autosamplerv_position = int(positions)
#             self.autosampler_valve.set_position(self.autosamplerv_position)

#         self.running_event.clear()

#     def set_pump_aspirate_rates(self, rates, pump='all'):
#         if pump == 'all':
#             self.pump_sample.refill_rate = rates[0]
#             self.pump_buffer.refill_rate = rates[1]
#         elif pump == 'sample':
#             self.pump_sample.refill_rate == rates
#         elif pump == 'buffer':
#             self.pump_buffer.refill_rate = rates

#     def set_pump_dispense_rates(self, rates, pump='all'):
#         if pump == 'all':
#             self.pump_sample.flow_rate = rates[0]
#             self.pump_buffer.flow_rate = rates[1]
#         elif pump == 'sample':
#             self.pump_sample.flow_rate == rates
#         elif pump == 'buffer':
#             self.pump_buffer.flow_rate = rates

#     def set_pump_volumes(self, volumes, pump='all'):
#         if pump == 'all':
#             self.pump_sample.volume = volumes[0]
#             self.pump_buffer.volume = volumes[1]
#         elif pump == 'sample':
#             self.pump_sample.volume = volumes
#         elif pump == 'buffer':
#             self.pump_buffer.volume = volumes

#     def set_pump_offset_volumes(self, volumes, pump='all'):
#         if pump == 'all':
#             self.sample_offset_volume = volumes[0]
#             self.buffer_offset_volume = volumes[1]
#         elif pump == 'sample':
#             self.sample_offset_volume = volumes
#         elif pump == 'buffer':
#             self.buffer_offset_volume = volumes

#     def set_loop_volume(self, volume):
#         self.loop_volume = volume

#     def set_sample_overdraw(self, volume):
#         self.sample_overdraw = volume


#     def aspirate(self, volume, pump, blocking=True):
#         self.running_event.set()
#         abort = False

#         if pump == 'sample':
#             selected_pump = self.pump_sample
#         elif pump == 'buffer':
#             selected_pump = self.pump_buffer

#         initial_volume = selected_pump.volume

#         selected_pump.aspirate(volume)

#         if blocking:
#             while selected_pump.is_moving():
#                 time.sleep(0.05)
#                 abort = self._check_abort()
#                 if abort:
#                     break

#             self.running_event.clear()

#             final_volume = selected_pump.volume

#             if round(initial_volume + volume, 4) != round(final_volume, 4):
#                 logger.error('Pump %s failed to aspirate requested '
#                     'volume! Volume requested: %f, volume '
#                     'aspirated: %f', pump, volume,
#                     final_volume-initial_volume)
#                 raise Exception('Pump aspirate failed!')

#         else:
#             abort = False

#         return not abort

#     def aspirate_all(self, pump, blocking=True):
#         self.running_event.set()
#         abort = False

#         if pump == 'sample':
#             selected_pump = self.pump_sample
#         elif pump == 'buffer':
#             selected_pump = self.pump_buffer

#         selected_pump.aspirate_all()

#         if blocking:
#             while selected_pump.is_moving():
#                 time.sleep(0.05)
#                 abort = self._check_abort()
#                 if abort:
#                     break

#             self.running_event.clear()

#         else:
#             abort = False

#         return not abort

#     def dispense(self, volume, pump, blocking=True):
#         self.running_event.set()
#         abort = False

#         if pump == 'sample':
#             selected_pump = self.pump_sample
#         elif pump == 'buffer':
#             selected_pump = self.pump_buffer

#         initial_volume = selected_pump.volume

#         selected_pump.dispense(volume)

#         if blocking:
#             while selected_pump.is_moving():
#                 time.sleep(0.05)
#                 abort = self._check_abort()
#                 if abort:
#                     break

#             self.running_event.clear()

#             final_volume = selected_pump.volume

#             if round(initial_volume - final_volume, 4) != round(volume, 4):
#                 logger.error('Pump %s failed to dispense requested '
#                     'volume! Volume requested: %f, volume dispensed: %f',
#                     pump, volume, initial_volume - final_volume)
#                 raise Exception('Pump dispense failed!')

#         else:
#             abort = False



#         return not abort

#     def dispense_all(self, pump, blocking=True):
#         self.running_event.set()
#         abort = False

#         if pump == 'sample':
#             selected_pump = self.pump_sample
#         elif pump == 'buffer':
#             selected_pump = self.pump_buffer

#         selected_pump.dispense_all()

#         if blocking:
#             while selected_pump.is_moving():
#                 time.sleep(0.05)
#                 abort = self._check_abort()
#                 if abort:
#                     break

#             self.running_event.clear()

#         else:
#             abort = False

#         return not abort

#     def load_buffer(self, volume, row, column):
#         self.process_event.set()

#         logger.info("Starting buffer load")

#         self.buffer_volume = volume

#         success = self.move_to_load(row, column)

#         if success:
#             logger.info("Loading buffer")

#             self.set_valve_positions(2, 'bypass')
#             self.set_valve_positions(1, 'buffer')
#             self.set_valve_positions(2, 'autosampler')

#             success = self.aspirate(volume, 'buffer')

#         if success:
#             self._sleep(3)
#             success = self.move_to_z_out()
#             if success:
#                 logger.info("Pulling buffer into loop")
#                 success = self.aspirate(self.buffer_offset_volume, 'buffer')

#         if success:
#             logger.info("Expelling extra air")

#             self.set_valve_positions(1, 'bypass')
#             self.set_pump_dispense_rates(0.5, 'buffer')
#             success = self.dispense(self.buffer_offset_volume, 'buffer')
#             self.set_pump_dispense_rates(self.settings['pump_rates']['buffer'][1],
#                 'buffer')
#             self._sleep(3)
#             self.set_valve_positions(2, 'bypass')

#         if success:
#             logger.info("Flushing line to needle")

#             self.set_valve_positions(2, 'injection')
#             self.set_valve_positions(2, 'buffer')

#             #Push enough buffer to flush coflow needle (20 uL or ~3x volume from buffer valve to needle)
#             success = self.dispense(self.buffer_flush_volume, 'buffer')
#         #Clean lines running to autosampler (by cleaning sample loop)?

#         self.buffer_volume = self.buffer_volume - self.buffer_flush_volume
#         self.process_event.clear()

#         return success

#     def load_sample(self, volume, row, column):
#         self.process_event.set()

#         logger.info("Starting sample load")

#         success = self.move_to_load(row, column)

#         if success:
#             self.set_valve_positions(2, 'injection')
#             self.set_valve_positions(1, 'sample')
#             self.set_valve_positions(3, 'autosampler')

#             success = self.aspirate(volume, 'sample')

#         if success:
#             self._sleep(3)
#             success = self.move_to_z_out()
#             if success:
#                 offset_volume = self.sample_offset_volume + self.sample_overdraw
#                 success = self.aspirate(offset_volume, 'sample')

#         self.process_event.clear()

#         logger.info("Sample load finished")

#         return success

#     def make_measurement(self):
#         #Flow rates ideally 100-200 uL/min?
#         self.process_event.set()
#         self.set_valve_positions(2, 'injection')
#         self.set_valve_positions(1, 'sample')
#         self.set_valve_positions(2, 'buffer')
#         self.set_valve_positions(2, 'bypass')

#         start_time = self.buffer_delay_volume/self.pump_buffer.refill_rate
#         start_time = start_time*60 #pump refill rate is in ml/min
#         # Start buffer flow and exposure
#         logger.info('Starting measurement dispense')

#         self.dispense(self.buffer_volume - self.buffer_reserve_volume, 'buffer', blocking=False)
#         abort = self._sleep(start_time)

#         if not abort:
#             # After ~10 uL, of buffer flow
#             logger.info('Switching injection valve to inject')
#             self.set_valve_positions(1, 'injection')
#             # Continue flow until almost out of buffer, then stop before running out
#             self.running_event.set()
#             while self.pump_buffer.is_moving():
#                 time.sleep(0.01)
#                 abort = self._check_abort()
#                 if abort:
#                     break

#             self.running_event.clear()

#         self.process_event.clear()

#         logger.info('Measurement finished')

#     def set_clean_sequence(self, seq, loop):
#         if loop == 'sample':
#             self.clean_sample_seq = seq
#         elif loop == 'buffer':
#             self.clean_buffer_seq = seq

#     def clean_sample(self):
#         self.process_event.set()
#         logger.info('Starting sample cleaning sequence')

#         success = self.move_to_clean()

#         if success:
#             self.set_valve_positions(2, 'injection')
#             self.set_valve_positions(3, 'autosampler')

#             self.set_valve_positions(1, 'sample')
#             if self.pump_sample.volume > 0:
#                 success = self.dispense_all('sample')

#             if success:
#                 for clean_step in self.clean_sample_seq:
#                     self.set_valve_positions(clean_step[0], 'sample')
#                     self.running_event.set()
#                     abort = self._sleep(clean_step[1])
#                     self.running_event.clear()
#                     if abort:
#                         break

#                 success = not abort

#             self.set_valve_positions(2, 'sample')

#         self.move_to_z_out()

#         self.process_event.clear()

#         return success

#     def clean_buffer(self):
#         self.process_event.set()
#         logger.info("Starting buffer cleaning sequence")
#         self.set_valve_positions(1, 'bypass')

#         if round(self.pump_buffer.volume, 4) > 0:
#             self.dispense_all('buffer', False)

#         for clean_step in self.clean_buffer_seq:
#             self.set_valve_positions(clean_step[0], 'buffer')
#             self.running_event.set()
#             abort = self._sleep(clean_step[1])
#             self.running_event.clear()
#             if abort:
#                 break

#         if not abort:
#             self.running_event.set()
#             while self.pump_buffer.is_moving():
#                 time.sleep(0.01)
#                 abort = self._check_abort()
#                 if abort:
#                     break

#             self.running_event.clear()

#         # HAVE TO THINK ABOUT HOW TO CLEAN LINE FROM AUTOSAMPLER
#         # TO BUFFER VALVE! COULD DRAW WATER INTO IT, THEN PUSH IT
#         # OUT WITH THE BUFFER PUMP TO MOSTLY CLEAN IT.

#         self.set_valve_positions(2, 'buffer')

#         self.process_event.clear()

#         return not abort

#     def _sleep(self, sleep_time):
#         start = time.time()

#         while time.time() - start < sleep_time:
#             time.sleep(0.01)
#             abort = self._check_abort()
#             if abort:
#                 break

#         return abort



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
        'needle_motor'          : {'name': 'needle_y', 'args': ['18ID_DMC_E05:33'],
                                        'kwargs': {}},
        'plate_x_motor'         : {'name': 'plate_x', 'args': ['18ID_DMC_E05:37'],
                                        'kwargs': {}},
        'plate_z_motor'         : {'name': 'plate_z', 'args': ['18ID_DMC_E05:34'],
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


