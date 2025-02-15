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

import traceback
import threading
import time
import collections
from collections import OrderedDict, deque
import queue
import logging
import sys

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import wx
import wx.lib.buttons as buttons
import numpy as np
import epics

try:
    import serial.tools.list_ports as list_ports
except ModuleNotFoundError:
    pass

try:
    import zaber.serial as zaber #pip install zaber.serial
except ModuleNotFoundError:
    pass

import XPS_C8_drivers as xps_drivers
import utils


class Motor(object):
    """
    """

    def __init__(self, device, name):
        """
        """

        self.device = device
        self.name = name

    def __repr__(self):
        return '{}({}, {})'.format(self.__class__.__name__, self.name, self.device)

    def __str__(self):
        return '{} {}, connected to {}'.format(self.__class__.__name__, self.name, self.device)

    @property
    def position(self):
        """
        Sets and returns the pump flow rate in units specified by ``Pump.units``.
        Can be set while the pump is moving, and it will update the flow rate
        appropriately.

        :type: float
        """
        pass #Should be implimented in each subclass

    @position.setter
    def position(self, position):
        pass #Should be implimented in each subclass

    @property
    def units(self):
        """
        Sets and returns the pump flow rate units. This can be set to:
        nL/s, nL/min, uL/s, uL/min, mL/s, mL/min. Changing units keeps the
        flow rate constant, i.e. if the flow rate was set to 100 uL/min, and
        the units are changed to mL/min, the flow rate is set to 0.1 mL/min.

        :type: str
        """
        return self._units

    @units.setter
    def units(self, units):
        old_units = self._units

        if units in ['um/s', 'um/min', 'mm/s', 'mm/min', 'm/s', 'm/min']:
            self._units = units
            old_vu, old_tu = old_units.split('/')
            new_vu, new_tu = self._units.split('/')
            if old_vu != new_vu:
                if (old_vu == 'um' and new_vu == 'mm') or (old_vu == 'mm' and new_vu == 'm'):
                    self._scale = self._scale/1000.
                elif old_vu == 'um' and new_vu == 'm':
                    self._scale = self._scale/1000000.
                elif (old_vu == 'm' and new_vu == 'mm') or (old_vu == 'mm' and new_vu == 'um'):
                    self._scale = self._scale*1000.
                elif old_vu == 'm' and new_vu == 'um':
                    self._scale = self._scale*1000000.
            if old_tu != new_tu:
                if old_tu == 'min':
                    self._scale = self._scale/60
                else:
                    self._scale = self._scale*60

            logger.info("Changed motor %s units from %s to %s", self.name, old_units, units)
        else:
            logger.warning("Failed to change motor %s units, units supplied were invalid: %s", self.name, units)


    def send_cmd(self, cmd, get_response=True):
        """
        Sends a command to the pump.

        :param cmd: The command to send to the pump.

        :param get_response: Whether the program should get a response from the pump
        :type get_response: bool
        """
        pass #Should be implimented in each subclass


    def is_moving(self):
        """
        Queries the pump about whether or not it's moving.

        :returns: True if the pump is moving, False otherwise
        :rtype: bool
        """
        pass #Should be implimented in each subclass

    def move_relative(self):
        pass #Should be implimented in each subclass

    def move_absolute(self):
        pass #Should be implimented in each subclass

    def home(self):
        pass #should be implimented in each subclass

    def get_high_limit(self):
        pass #should be implimented in each subclass

    def set_high_limit(self, limit):
        pass

    def get_low_limit(self):
        pass #should be implimented in each subclass

    def set_low_limit(self, limit):
        pass

    def get_limits(self):
        pass

    def set_limits(self, low_lim, high_lim):
        pass

    def get_velocity(self):
        pass #should be implimented in each subclass

    def set_velocity(self, velocity):
        pass

    def get_acceleration(self):
        pass #should be implimented in each subclass

    def set_acceleration(self, acceleration):
        pass

    def stop(self):
        """Stops all pump flow."""
        pass #Should be implimented in each subclass

    def disconnect(self):
        """Close any communication connections"""
        pass #Should be implimented in each subclass

class NewportXPSMotor(Motor):
    """
    """

    def __init__(self, name, xps, ip_address, port, timeout, group, num_axes,
        is_hxp=False):
        """
        """

        Motor.__init__(self, '{}:{}'.format(ip_address, port), name)

        self.ip_address = ip_address
        self.port = int(port)
        self.timeout = timeout
        self.group = group
        self.num_axes = num_axes

        self.xps = xps
        self.is_hxp = is_hxp

        self.sockets = {}

        self.connect_to_xps('general')
        self.connect_to_xps('status')
        self.connect_to_xps('move')

        if 'status' in self.sockets:
            controller_status, descrip = self.get_controller_status()

            if controller_status == 0:
                group_status, descrip = self.get_group_status()
            else:
                group_status = -1

            if group_status == 0:
                logger.info('Initializing %s', self.group)
                error, ret = self.xps.GroupInitialize(self.sockets['general'], self.group)

                if error != 0:
                    self.get_error('general', self.sockets['general'], error, ret)

        self._offset = [0. for i in range(num_axes)]
        self._scale = 1
        self._units = 'mm/s'

    def connect_to_xps(self, socket_name):
        logger.debug('%s connecting to the XPS at %s:%i', self.name, self.ip_address, self.port)
        my_socket = self.xps.TCP_ConnectToServer(self.ip_address, self.port, self.timeout)

        if my_socket != -1:
            logger.info('%s connected to the XPS at %s:%i on socket %i', self.name,
                self.ip_address, self.port, my_socket)
            self.sockets[socket_name] = my_socket
        else:
            logger.error('%s failed to connect to the XPS at %s:%i', self.name, self.ip_address, self.port)

    def get_error(self, socket_name, socket_id, error_code, ret_str):
        error, descrip = self.xps.ErrorStringGet(socket_id, str(error_code))

        logger.error('Error on socket %s (%i): CMD: %s, ERR: %s', socket_name, socket_id, ret_str, descrip)

    def get_group_status(self, positioner=None):
        if positioner is None:
            positioner = self.group

        error, group_status = self.xps.GroupStatusGet(self.sockets['status'], positioner)

        if error != 0:
            self.get_error('status', self.sockets['status'], error, group_status)
            group_status = None
            descrip = None

        else:
            error, descrip = self.xps.GroupStatusStringGet(self.sockets['status'], group_status)

            if error !=0:
                self.get_error('status', self.sockets['status'], error, descrip)
            else:
                # logger.debug('Group status: %i - %s', group_status, descrip)
                pass

        return group_status, descrip

    def get_controller_status(self):
        error, controller_status = self.xps.ControllerStatusGet(self.sockets['status'])

        if error != 0:
            self.get_error('status', self.sockets['status'], error, controller_status)
            controller_status = None
            descrip = None

        else:
            error, descrip = self.xps.ControllerStatusStringGet(self.sockets['status'], controller_status)
            if error != 0:
                self.get_error('status', self.sockets['status'], error, descrip)
            else:
                logger.info('Controller status: %i - %s', controller_status, descrip)

        return controller_status, descrip


    @property
    def position(self):
        """
        Sets and returns the pump flow rate in units specified by ``Pump.units``.
        Can be set while the pump is moving, and it will update the flow rate
        appropriately.

        :type: float
        """
        logger.debug('Getting %s position', self.group)
        ret = self.xps.GroupPositionCurrentGet(self.sockets['status'],
            self.group, self.num_axes)

        error = ret[0]

        if error != 0:
            self.get_error('status', self.sockets['status'], error, ret[1])
            positions = []

        else:
            positions = ret[1:]
            logger.debug('{} position(s): {}'.format(self.group, positions))

            try:
                for i in range(len(positions)):
                    positions[i] = positions[i]*self._scale + self._offset[i]
            except TypeError:
                positions = positions*self._scale + self._offset[0]

            logger.debug('{} user position(s): {}'.format(self.group, positions))

        return positions

    @position.setter
    def position(self, setpoint_positions):
        logger.debug('Setting %s position', self.group)
        ret = self.xps.GroupPositionCurrentGet(self.sockets['status'],
            self.group, self.num_axes)

        error = ret[0]

        if error != 0:
            self.get_error('status', self.sockets['status'], error, ret[1])
            positions = []

        else:
            positions = ret[1:]
            logger.debug('Current {} position(s): {}'.format(self.group, positions))
            logger.debug('Setpoint {} position(s): {}'.format(self.group, setpoint_positions))

            try:
                for i in range(len(positions)):
                    self._offset[i] = setpoint_positions[i]-positions[i]*self._scale
            except TypeError:
                self._offset[0] = setpoint_positions[0]-positions*self._scale

            logger.debug('Set offsets for positions')

    def get_positioner_position(self, positioner, index):

        # print (self.sockets)

        logger.debug('Getting %s position', positioner)
        error, positions = self.xps.GroupPositionCurrentGet(self.sockets['status'],
            positioner, 1)

        if error != 0:
            self.get_error('status', self.sockets['status'], error, positions)
            positions = []

        else:
            logger.debug('{} position: {}'.format(self.group, positions))
            positions = positions*self._scale + self._offset[index]
            logger.debug('{} user position: {}'.format(self.group, positions))

        return positions

    def set_positioner_position(self, positioner, index, setpoint_position):
        logger.debug('Setting %s position', positioner)
        error, position = self.xps.GroupPositionCurrentGet(self.sockets['status'],
            positioner, 1)

        if error != 0:
            self.get_error('status', self.sockets['status'], error, position)
        else:
            logger.debug('Current %s position: %f', positioner, position)
            logger.debug('Setpoint %s position: %f', positioner, setpoint_position)
            self._offset[index] = setpoint_position - position*self._scale

            logger.debug('Set offset for position')


    @property
    def units(self):
        """
        Sets and returns the pump flow rate units. This can be set to:
        nL/s, nL/min, uL/s, uL/min, mL/s, mL/min. Changing units keeps the
        flow rate constant, i.e. if the flow rate was set to 100 uL/min, and
        the units are changed to mL/min, the flow rate is set to 0.1 mL/min.

        :type: str
        """
        return self._units

    @units.setter
    def units(self, units):
        old_units = self._units

        if units in ['um/s', 'um/min', 'mm/s', 'mm/min', 'm/s', 'm/min']:
            self._units = units
            old_vu, old_tu = old_units.split('/')
            new_vu, new_tu = self._units.split('/')
            if old_vu != new_vu:
                if (old_vu == 'um' and new_vu == 'mm') or (old_vu == 'mm' and new_vu == 'm'):
                    self._scale = self._scale/1000.
                elif old_vu == 'um' and new_vu == 'm':
                    self._scale = self._scale/1000000.
                elif (old_vu == 'm' and new_vu == 'mm') or (old_vu == 'mm' and new_vu == 'um'):
                    self._scale = self._scale*1000.
                elif old_vu == 'm' and new_vu == 'um':
                    self._scale = self._scale*1000000.
            if old_tu != new_tu:
                if old_tu == 'min':
                    self._scale = self._scale/60
                else:
                    self._scale = self._scale*60

            logger.info("Changed motor %s units from %s to %s", self.name, old_units, units)
        else:
            logger.warning("Failed to change motor %s units, units supplied were invalid: %s", self.name, units)

    def is_moving(self):
        """
        Queries the pump about whether or not it's moving.

        :returns: True if the pump is moving, False otherwise
        :rtype: bool
        """
        status, descrip = self.get_group_status()

        if (status >= 43 and status <= 45) or status == 47:
            result = True
        else:
            result = False

        return result

    def positioner_is_moving(self, positioner):
        status, descrip = self.get_group_status(positioner)

        if (status >= 43 and status <= 45) or status == 47:
            result = True
        else:
            result = False

        return result

    def move_relative(self, displacements, positioner=None, index=0):
        if positioner is None:
            positioner = self.group

        log_str = 'Moving motor {} by {}'.format(positioner, displacements)
        logger.debug(log_str)

        if len(displacements) == 1:
            cor_displacements = [(displacements[0]-self._offset[index])/self._scale]
        else:
            cor_displacements = []
            for i, disp in enumerate(displacements):
                cor_displacements.append((disp-self._offset[i])/self._scale)

        if not self.is_hxp:
            error, ret = self.xps.GroupMoveRelative(self.sockets['move'], positioner, cor_displacements)
        else:
            error, ret = self.xps.HexapodMoveRelative(self.sockets['move'], positioner, cor_displacements)

        if error != 0:
            self.get_error('move', self.sockets['move'], error, ret)
            success = False
        else:
            success = True
            log_str = 'Moved motor {} by {}'.format(positioner, displacements)
            logger.info(log_str)

        return success

    def move_absolute(self, positions, positioner=None, index=0):
        if positioner is None:
            positioner = self.group

        log_str = 'Moving motor {} to {}'.format(positioner, positions)
        logger.debug(log_str)

        if len(positions) == 1:
            cor_positions = [(positions[0]-self._offset[index])/self._scale]
        else:
            cor_positions = []
            for i, pos in enumerate(positions):
                cor_positions.append((pos-self._offset[i])/self._scale)

        if not self.is_hxp:
            error, ret = self.xps.GroupMoveAbsolute(self.sockets['move'], positioner, cor_positions)
        else:
            error, ret = self.xps.HexapodMoveAbsolute(self.sockets['move'], positioner, cor_positions)

        if error != 0:
            self.get_error('move', self.sockets['move'], error, ret)
            success = False
        else:
            success = True
            log_str = 'Moved motor {} to {}'.format(positioner, positions)
            logger.info(log_str)

        return success

    def move_positioner_absolute(self, positioner, index, position):
        if not isinstance(position, list):
            position = [position]

        success = self.move_absolute(position, positioner, index)
        return success

    def move_positioner_relative(self, positioner, index, displacement):
        if not isinstance(displacement, list):
            displacement = [displacement]

        success = self.move_relative(displacement, positioner, index)
        return success

    def home(self, positioner=None):
        if positioner is None:
            positioner = self.group
        logger.debug('Homing motor  %s', positioner)
        error, ret = self.xps.GroupHomeSearch(self.sockets['move'], positioner)

        if error != 0:
            self.get_error('move', self.sockets['move'], error, ret)
            success = False
        else:
            success = True
            logger.info('Homed motor %s', positioner)

        return success

    def home_positioner(self, positioner):
        success = self.home(positioner)

        return success

    def get_high_limit(self, positioner, index):
        logger.debug('Getting %s high limit', positioner)

        ret = self.xps.PositionerUserTravelLimitsGet(self.sockets['general'],
            positioner)

        error = ret[0]

        if error != 0:
            self.get_error('general', self.sockets['general'], error, ret[1])
            high_lim = None
        else:
            high_lim = ret[2]
            logger.info('%s high limit %f', positioner, high_lim)
            high_lim = high_lim*self._scale+self._offset[index]
            logger.info('%s user high limit %f', positioner, high_lim)

        return high_lim


    def set_high_limit(self, limit,  positioner, index):
        logger.debug('Setting %s high limit', positioner)

        ret = self.xps.PositionerUserTravelLimitsGet(self.sockets['general'],
            positioner)

        error = ret[0]

        if error != 0:
            self.get_error('general', self.sockets['general'], error, ret[1])
            success = False
        else:
            low_lim = ret[1]

            error, ret = self.xps.PositionerUserTravelLimitsSet(self.sockets['general'],
                positioner, low_lim, (limit-self._offset[index])/self._scale)

            if error != 0:
                self.get_error('general', self.sockets['general'], error, ret)
                success = False
            else:
                success = True

                logger.debug('Set %s high limit to %f', positioner, (limit-self._offset[index])/self._scale)
                logger.debug('Set user %s high limit to %f', positioner, limit)

        return success

    def get_low_limit(self, positioner, index):
        logger.debug('Getting %s low limit', positioner)

        ret = self.xps.PositionerUserTravelLimitsGet(self.sockets['general'],
            positioner)

        error = ret[0]

        if error != 0:
            self.get_error('general', self.sockets['general'], error, ret[1])
            low_lim = None
        else:
            low_lim = ret[1]

            logger.info('%s low limit %f', positioner, low_lim)
            low_lim = low_lim*self._scale+self._offset[index]
            logger.info('%s user high limit %f', positioner, low_lim)

        return low_lim

    def set_low_limit(self, limit, positioner, index):
        logger.debug('Setting %s high limit', positioner)

        ret = self.xps.PositionerUserTravelLimitsGet(self.sockets['general'],
            positioner)

        error = ret[0]

        if error != 0:
            self.get_error('general', self.sockets['general'], error, ret[1])
            success = False
        else:
            high_lim = ret[2]

            error, ret = self.xps.PositionerUserTravelLimitsSet(self.sockets['general'],
                positioner, (limit-self._offset[index])/self._scale, high_lim)

            if error != 0:
                self.get_error('general', self.sockets['general'], error, ret)
                success = False
            else:
                success = True

                logger.debug('Set %s low limit to %f', positioner, (limit-self._offset[index])/self._scale)
                logger.debug('Set user %s low limit to %f', positioner, limit)

        return success

    def get_limits(self, positioner, index):
        logger.debug('Getting %s limits', positioner)

        ret = self.xps.PositionerUserTravelLimitsGet(self.sockets['general'],
            positioner)

        error = ret[0]

        if error != 0:
            self.get_error('general', self.sockets['general'], error, ret[1])
            low_lim = None
            high_lim = None
        else:
            low_lim = ret[1]

            logger.info('%s low limit %f', positioner, low_lim)
            low_lim = low_lim*self._scale+self._offset[index]
            logger.info('%s user high limit %f', positioner, low_lim)

            high_lim = ret[2]
            logger.info('%s high limit %f', positioner, high_lim)
            high_lim = high_lim*self._scale+self._offset[index]
            logger.info('%s user high limit %f', positioner, high_lim)

        return low_lim, high_lim

    def set_limits(self, low_lim, high_lim, positioner, index):
        logger.debug('Setting %s high limit', positioner)

        error, ret = self.xps.PositionerUserTravelLimitsSet(self.sockets['general'],
            positioner, (low_lim-self._offset[index])/self._scale,
            (high_lim-self._offset[index])/self._scale)

        if error != 0:
            self.get_error('general', self.sockets['general'], error, ret)
            success = False
        else:
            success = True

        return success

    def get_velocity(self, positioner, index):
        logger.debug('Getting %s velocity', positioner)

        ret = self.xps.PositionerSGammaParametersGet(self.sockets['general'],
            positioner)

        error = ret[0]

        if error != 0:
            self.get_error('general', self.sockets['general'], error, ret[1])
            velocity = None
        else:
            velocity = ret[1]

            logger.info('%s velocity is %f', positioner, velocity)
            velocity = velocity*self._scale
            logger.info('%s user velocity is %f', positioner, velocity)

        return velocity

    def set_velocity(self, velocity, positioner, index,):
        logger.debug('Getting %s velocity', positioner)

        ret = self.xps.PositionerSGammaParametersGet(self.sockets['general'],
            positioner)

        error = ret[0]

        if error != 0:
            self.get_error('general', self.sockets['general'], error, ret[1])
            success = False

        else:
            error, ret = self.xps.PositionerSGammaParametersSet(self.sockets['general'],
                positioner, velocity/self._scale, ret[2], ret[3], ret[4])

            if error != 0:
                self.get_error('general', self.sockets['general'], error, ret[1])
                success = False
            else:
                success = True

                logger.info('%s velocity set to %f', positioner, velocity/self._scale)
                logger.info('%s user velocity set to %f', positioner, velocity)

        return success

    def get_acceleration(self, positioner, index):
        logger.debug('Getting %s acceleration', positioner)

        ret = self.xps.PositionerSGammaParametersGet(self.sockets['general'],
            positioner)

        error = ret[0]

        if error != 0:
            self.get_error('general', self.sockets['general'], error, ret[1])
            acceleration = None
        else:
            acceleration = ret[2]

            logger.info('%s acceleration is %f', positioner, acceleration)
            acceleration = acceleration*self._scale
            logger.info('%s user acceleration is %f', positioner, acceleration)

        return acceleration

    def set_acceleration(self, acceleration, positioner, index):
        logger.debug('Setting %s acceleration', positioner)

        ret = self.xps.PositionerSGammaParametersGet(self.sockets['general'],
            positioner)

        error = ret[0]

        if error != 0:
            self.get_error('general', self.sockets['general'], error, ret[1])
            success = False

        else:
            error, ret = self.xps.PositionerSGammaParametersSet(self.sockets['general'],
                positioner, ret[1], acceleration/self._scale, ret[3], ret[4])

            if error != 0:
                self.get_error('general', self.sockets['general'], error, ret[1])
                success = False
            else:
                success = True

                logger.info('%s acceleration set to %f', positioner, acceleration/self._scale)
                logger.info('%s user acceleration set to %f', positioner, acceleration)

        return success

    # Don't have a positioner I can test this with
    # def get_hard_interpolation(self, positioner):
    #     logger.debug('Getting encoder hardware interpolation factor')
    #     ret = self.xps.PositionerHardInterpolatorFactorGet(self.sockets['general'], positioner)

    #     error = ret[0]

    #     if error != 0:
    #         self.get_error('general', self.sockets['general'], error, ret[1])

    #     else:
    #         logger.info(ret[1:])

    def get_position_compare(self, positioner, index):
        logger.debug('Getting %s position compare settings', positioner)

        ret = self.xps.PositionerPositionCompareGet(self.sockets['general'],
            positioner)

        error = ret[0]

        if error != 0:
            self.get_error('general', self.sockets['general'], error, ret[1])
            min_pos = None
            max_pos = None
            step = None
            enable = None
        else:
            min_pos = ret[1]
            max_pos = ret[2]
            step = ret[3]
            enable = ret[4]
            logger.info('Got %s position compare settings: min: %f, max: %f, step: %f, enable: %i',
                positioner, min_pos, max_pos, step, enable)

            min_pos = min_pos*self._scale - self._offset[index]
            max_pos = max_pos*self._scale - self._offset[index]
            step = step*self._scale - self._offset[index]
            enable = ret[4]
            logger.info('Got user %s position compare settings: min: %f, max: %f, step: %f, enable: %i',
                positioner, min_pos, max_pos, step, enable)

        return min_pos, max_pos, step, enable

    def set_position_compare(self, positioner, index, min_position, max_position, position_step):
        """
        Note: Needs to be run when position compare is not enabled. For bidirectional
        accuracy, the difference between the min_position and max_position should
        be a multiple of the position step. All three positions are rounded to the
        nearest detectable trigger position. For AquadB encoders such as those on
        our Newport ILS motors, this is the encoder resolution (0.5 um)
        """
        min_position = float(min_position)
        max_position = float(max_position)
        position_step = float(position_step)

        logger.debug('Setting %s position compare settings', positioner)

        error, ret = self.xps.PositionerPositionCompareSet(self.sockets['general'],
            positioner, (min_position-self._offset[index])/self._scale,
            (max_position-self._offset[index])/self._scale,
            (position_step-self._offset[index])/self._scale)

        if error != 0:
            self.get_error('general', self.sockets['general'], error, ret)
            success = False
        else:
            success = True
            logger.info('Set %s position compare settings: min: %f, max: %f, step: %f',
                positioner, min_position, max_position, position_step)
            logger.info('Set %s user position compare settings: min: %f, max: %f, step: %f',
                positioner, (min_position-self._offset[index])/self._scale,
                (max_position-self._offset[index])/self._scale,
                (position_step-self._offset[index])/self._scale)

        return success

    def start_position_compare(self, positioner):
        logger.debug('Starting %s position compare', positioner)

        error, ret = self.xps.PositionerPositionCompareEnable(self.sockets['general'],
            positioner)

        if error != 0:
            self.get_error('general', self.sockets['general'], error, ret)
            success = False
        else:
            success = True
            logger.info('Started %s position compare', positioner)

        return success

    def stop_position_compare(self, positioner):
        logger.debug('Stopping %s position compare', positioner)

        error, ret = self.xps.PositionerPositionCompareDisable(self.sockets['general'],
            positioner)

        if error != 0:
            self.get_error('general', self.sockets['general'], error, ret)
            success = False
        else:
            success = True
            logger.info('Stopped %s position compare', positioner)

        return success

    def get_position_compare_pulse(self, positioner):
        """
        Pulse width in us
        Encoder signal settling time in us
        """
        logger.debug('Getting %s position compare pulse parameters', positioner)

        ret = self.xps.PositionerPositionComparePulseParametersGet(self.sockets['general'],
            positioner)

        error = ret[0]

        if error != 0:
            self.get_error('general', self.sockets['general'], error, ret[1])
            pulse_width = None
            encoder_settle_time = None
        else:
            pulse_width = ret[1]
            encoder_settle_time = ret[2]
            logger.info('Got %s position compare pulse parameters: pulse ' \
                'width [us]: %s, encoder settling time [us]: %s', positioner, \
                pulse_width, encoder_settle_time)

        return pulse_width, encoder_settle_time

    def set_position_compare_pulse(self, positioner, pulse_width, encoder_settle_time):
        """
        Pulse width in us, options: 0.2 (default), 1, 2, 5, or 10
        Encoder signal settling time in us, options: 0.075 (default), 1, 4, or 12
        """
        pulse_width = float(pulse_width)
        encoder_settle_time = float(encoder_settle_time)

        logger.debug('Setting %s position compare pulse parameters', positioner)

        ret = self.xps.PositionerPositionComparePulseParametersSet(self.sockets['general'],
            positioner, pulse_width, encoder_settle_time)

        error = ret[0]

        if error != 0:
            self.get_error('general', self.sockets['general'], error, ret[1])
            success = False
        else:
            success = True
            logger.info('Set %s position compare pulse parameters: pulse ' \
                'width [us]: %s, encoder settling time [us]: %s', positioner, \
                pulse_width, encoder_settle_time)


        return success


    def stop(self, positioner=None):
        if positioner is None:
            positioner = self.group
        """Stops all pump flow."""
        self.xps.GroupMoveAbort(self.sockets['general'], positioner)

    def stop_positioner(self, positioner):
        self.stop(positioner)

    def disconnect(self):
        """Close any communication connections"""
        for socket_id in self.sockets.values():
            logger.info('%s disconnecting from the XPS at %s:%i on socket %s', self.name,
                self.ip_address, self.port, socket_id)
            self.xps.TCP_CloseSocket(socket_id)

class NewportXPSSingleAxis(object):

    def __init__(self, name, xps, ip_address, port, timeout, group, num_axes,
        axis, index):

        self.newport_motor = NewportXPSMotor(name, xps, ip_address, port, timeout, group, num_axes)
        self.axis = axis
        self.index = index

    def get_position(self):
        return self.newport_motor.get_positioner_position(self.axis, self.index)

    def move_absolute(self, pos):
        return self.newport_motor.move_positioner_absolute(self.axis, self.index, pos)

    def move_relative(self, pos):
        return self.newport_motor.move_positioner_relative(self.axis, self.index, pos)

    def is_busy(self):
        return self.newport_motor.positioner_is_moving(self.axis)

    def stop(self):
        self.newport_motor.stop(self.axis)

class ZaberMotor(object):
    """
    This class contains the settings and communication for a generic pump.
    It is intended to be subclassed by other pump classes, which contain
    specific information for communicating with a given pump. A pump object
    can be wrapped in a thread for using a GUI, implimented in :py:class:`PumpCommThread`
    or it can be used directly from the command line. The :py:class:`M5Pump`
    documentation contains an example.
    """

    def __init__(self, device, name, binary_serial, lock, device_number, travel,
        step_conversion=1.984375e-3):
        """
        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str
        """

        self.device = device
        self.name = name
        self.serial = binary_serial
        self.lock = lock
        self.number = device_number
        self.step_conversion = step_conversion

        self._offset = 0.
        self._scale = 1
        self._units = 'mm/s'

        self._high_lim = (travel-self._offset)/self._scale/self.step_conversion
        self._low_lim = 0

        self._initialize()

    def __repr__(self):
        return '{}({}, {})'.format(self.__class__.__name__, self.name, self.device)

    def __str__(self):
        return '{} {}, connected to {}'.format(self.__class__.__name__, self.name, self.device)

    def _initialize(self):
        mode = 0
        mode = mode + 2**1 #Anti-backlash
        mode = mode + 2**2 #Anti-sticktion
        mode = mode + 2**3 #Disable Potentiometer (manual adjustment)
        # mode = mode + 2**8 #Disable Auto-Home (Doesn't work!)
        mode = mode + 2**11 #Enable circular phase microstepping mode
        # mode = mode + 2**14 #Disable power LED

        self.send_cmd(40, mode)

        self.set_velocity(75)
        self.set_home_velocity(10)
        self.set_acceleration(500)


    @property
    def position(self):
        """
        Sets and returns the motor flow rate in units specified by ``motor.units``.
        Can be set while the motor is moving, and it will update the flow rate
        appropriately.

        :type: float
        """
        position, _ = self.send_cmd(60)

        position = position*self.step_conversion*self._scale + self._offset

        return position

    @position.setter
    def position(self, position):
        cur_position, _ = self.send_cmd(60)
        cur_position = cur_position*self.step_conversion*self._scale

        self._offset = position - cur_position

    @property
    def units(self):
        """
        Sets and returns the pump flow rate units. This can be set to:
        nL/s, nL/min, uL/s, uL/min, mL/s, mL/min. Changing units keeps the
        flow rate constant, i.e. if the flow rate was set to 100 uL/min, and
        the units are changed to mL/min, the flow rate is set to 0.1 mL/min.

        :type: str
        """
        return self._units

    @units.setter
    def units(self, units):
        old_units = self._units

        if units in ['um/s', 'um/min', 'mm/s', 'mm/min', 'm/s', 'm/min']:
            self._units = units
            old_vu, old_tu = old_units.split('/')
            new_vu, new_tu = self._units.split('/')
            if old_vu != new_vu:
                if (old_vu == 'um' and new_vu == 'mm') or (old_vu == 'mm' and new_vu == 'm'):
                    self._scale = self._scale/1000.
                elif old_vu == 'um' and new_vu == 'm':
                    self._scale = self._scale/1000000.
                elif (old_vu == 'm' and new_vu == 'mm') or (old_vu == 'mm' and new_vu == 'um'):
                    self._scale = self._scale*1000.
                elif old_vu == 'm' and new_vu == 'um':
                    self._scale = self._scale*1000000.
            if old_tu != new_tu:
                if old_tu == 'min':
                    self._scale = self._scale/60
                else:
                    self._scale = self._scale*60

            logger.info("Changed motor %s units from %s to %s", self.name, old_units, units)
        else:
            logger.warning("Failed to change motor %s units, units supplied were invalid: %s", self.name, units)


    def send_cmd(self, cmd_num, cmd_data=None, get_response=True, check_error=True):
        """
        Sends a command to the pump.

        :param cmd: The command to send to the pump.

        :param get_response: Whether the program should get a response from the pump
        :type get_response: bool
        """
        if cmd_data is not None:
            cmd = zaber.BinaryCommand(self.number, cmd_num, cmd_data)
        else:
            cmd = zaber.BinaryCommand(self.number, cmd_num)

        self.lock.acquire()
        self.serial.lock.acquire()
        try:
            self.serial.open()
            if get_response:
                while self.serial.can_read():
                    self.serial.read()

            logger.debug("Motor {} sending command {} with data {}".format(self.name, cmd_num, cmd_data))
            self.serial.write(cmd)

            if get_response:
                while not self.serial.can_read():
                    time.sleep(0.01)

                reply = self.serial.read()

            else:
                reply = None

        except Exception:
            raise
        finally:
            self.serial.close()

        self.serial.lock.release()
        self.lock.release()

        if reply is not None and check_error:
            success = self.check_command_succeeded(reply, self.name)
            data = reply.data
        else:
            success = True
            data = None

        return data, success


    @classmethod
    def check_command_succeeded(cls, reply, name=None):
        """
        Return true if command succeeded, print reason and return false if command
        rejected

        param reply: BinaryReply

        return: boolean
        """
        if reply.command_number == 255: # 255 is the binary error response code.
            if name is not None:
                logger.error("Motor {} command rejected. Error code: {}".format(name, reply.data))
            else:
                logger.error("Motor command rejected. Error code: "+ str(reply.data))
            return False
        else: # Command was accepted
            return True

    def get_status(self):
        status, success = self.send_cmd(54)

        return status

    def is_moving(self):
        """
        Queries the pump about whether or not it's moving.

        :returns: True if the pump is moving, False otherwise
        :rtype: bool
        """
        status = self.get_status()

        if (status == 1 or status == 10 or status == 18 or status == 20 or
            status ==21 or status == 22 or status == 23):
            moving = True
        else:
            moving = False

        return moving

    def move_relative(self, displacement, blocking=True):
        steps = (displacement-self._offset)/self._scale/self.step_conversion
        steps = int(round(steps))

        cur_pos = (self.position-self._offset)/self._scale/self.step_conversion

        if self._low_lim <= cur_pos+steps and cur_pos+steps <= self._high_lim:
            if blocking:
                _, success = self.send_cmd(21, steps)
            else:
                success = True
                self.send_cmd(21, steps, get_response=False)
        else:
            if self._low_lim >= cur_pos+steps:
                logger.error('Target position is <= the low limit')
            if cur_pos+steps >= self._high_lim:
                logger.error('Target position is >= the high limit')

            success = False

        return success

    def move_absolute(self, position, blocking=True):
        steps = (position-self._offset)/self._scale/self.step_conversion
        steps = int(round(steps))

        if self._low_lim <= steps and steps <= self._high_lim:
            if blocking:
                _, success = self.send_cmd(20, steps)
            else:
                success = True
                self.send_cmd(20, steps, get_response=False)
        else:
            if self._low_lim >= steps:
                logger.error('Target position is <= the low limit')
            if steps >= self._high_lim:
                logger.error('Target position is >= the high limit')

            success = False

        return success

    def home(self, blocking=True):
        if blocking:
            _, success = self.send_cmd(1)
        else:
            success = True
            self.send_cmd(1, get_response=False)

        return success

    def get_home_velocity(self):
        #Note: only works with firmware > 5.2
        velocity, success = self.send_cmd(53, 41)
        if success:
            velocity = velocity*9.375*self.step_conversion*self._scale
        else:
            velocity = None

        return velocity

    def set_home_velocity(self, velocity):
        #Note: only works with firmware > 5.2
        velocity = velocity/self._scale/self.step_conversion/9.375
        velocity = int(round(velocity))

        _, success = self.send_cmd(41, velocity)

        return success

    def get_high_limit(self):
        return self._high_lim*self.step_conversion*self._scale + self._offset

    def set_high_limit(self, limit):
        self._high_lim = (limit - self._offset)/self._scale/self.step_conversion

    def get_low_limit(self):
        return self._low_lim*self.step_conversion*self._scale + self._offset

    def set_low_limit(self, limit):
        self._low_lim = (limit - self._offset)/self._scale/self.step_conversion

    def set_limits(self, low_lim, high_lim):
        self.set_low_limit(low_lim)
        self.set_high_limit(high_lim)

    def get_limits(self):
        low_lim = self.get_low_limit()
        high_lim = self.get_high_limit()

        return low_lim, high_lim

    def get_velocity(self):
        velocity, success = self.send_cmd(53, 42)
        if success:
            velocity = velocity*9.375*self.step_conversion*self._scale
        else:
            velocity = None

        return velocity

    def set_velocity(self, velocity):
        velocity = velocity/self._scale/self.step_conversion/9.375
        velocity = int(round(velocity))

        _, success = self.send_cmd(42, velocity)

        return success

    def get_acceleration(self):
        accel, success = self.send_cmd(53, 43)
        if success:
            accel = accel*11250*self.step_conversion*self._scale
        else:
            accel = None

        return accel

    def set_acceleration(self, acceleration):
        acceleration = acceleration/self._scale/self.step_conversion/11250
        acceleration = int(round(acceleration))

        _, success = self.send_cmd(43, acceleration)

        return success

    def stop(self):
        _, success = self.send_cmd(23)

        return success

    def disconnect(self):
        """Close any communication connections"""
        pass #Should be implimented in each subclass

class EpicsMotor(Motor):
    """
    """

    def __init__(self, name, epics_pv):
        """
        """

        Motor.__init__(self, epics_pv, name)

        self.epics_motor = epics.Motor(epics_pv)

        self._offset = 0.
        self._scale = 1.
        self._units = 'mm/s'

    @property
    def position(self):
        pos = self.epics_motor.get_position()
        return float(pos)

    @position.setter
    def position(self, position):
        self.epics_motor.set_position(position)

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, units):
        old_units = self._units

        if units in ['um/s', 'um/min', 'mm/s', 'mm/min', 'm/s', 'm/min']:
            self._units = units
            old_vu, old_tu = old_units.split('/')
            new_vu, new_tu = self._units.split('/')
            if old_vu != new_vu:
                if (old_vu == 'um' and new_vu == 'mm') or (old_vu == 'mm' and new_vu == 'm'):
                    self._scale = self._scale/1000.
                elif old_vu == 'um' and new_vu == 'm':
                    self._scale = self._scale/1000000.
                elif (old_vu == 'm' and new_vu == 'mm') or (old_vu == 'mm' and new_vu == 'um'):
                    self._scale = self._scale*1000.
                elif old_vu == 'm' and new_vu == 'um':
                    self._scale = self._scale*1000000.
            if old_tu != new_tu:
                if old_tu == 'min':
                    self._scale = self._scale/60
                else:
                    self._scale = self._scale*60

            logger.info("Changed motor %s units from %s to %s", self.name, old_units, units)
        else:
            logger.warning("Failed to change motor %s units, units supplied were invalid: %s", self.name, units)


    def send_cmd(self, cmd, get_response=True):
        pass #Should be implimented in each subclass


    def is_moving(self):
        """
        Queries the pump about whether or not it's moving.

        :returns: True if the pump is moving, False otherwise
        :rtype: bool
        """
        mov = not self.epics_motor.get('done_moving')
        return mov

    def move_relative(self, displacement):
        self.epics_motor.move(displacement, relative=True)

    def move_absolute(self, position):
        self.epics_motor.move(position)

    def home(self):
        pass #should be implimented in each subclass

    def get_high_limit(self):
        hlim = self.epics_motor.get('high_limit')
        return float(hlim)

    def set_high_limit(self, limit):
        self.epics_motor.set('high_limit_set', limit)

    def get_low_limit(self):
        llim = self.epics_motor.get('low_limit')
        return float(llim)

    def set_low_limit(self, limit):
        self.epics_motor.put('low_limit_set', limit)

    def get_limits(self):
        llim = self.get_low_limit()
        hlim = self.get_high_limit()
        return llim, hlim

    def set_limits(self, low_lim, high_lim):
        self.set_low_limit(low_lim)
        self.set_high_limit(high_lim)

    def get_velocity(self):
        speed = self.epics_motor.get('slew_speed')
        return speed

    def set_velocity(self, velocity):
        self.epics_motor.put('slew_speed', velocity)

    def get_acceleration(self):
        accel_time = self.epics_motor.get('acceleration')
        speed = self.get_velocity()
        return speed/accel_time

    def set_acceleration(self, acceleration):
        speed = self.get_velocity()
        accel_time = speed/acceleration
        return accel_time

    def stop(self):
        self.epics_motor.put('stop', 1)

    def disconnect(self):
        """Close any communication connections"""
        pass #Should be implimented in each subclass

class MotorCommThread(threading.Thread):
    """
    This class creates a control thread for pumps attached to the system.
    This thread is designed for using a GUI application. For command line
    use, most people will find working directly with a pump object much
    more transparent. Below you'll find an example that initializes an
    :py:class:`M50Pump`, starts a flow of 2000 uL/min, and stops the flow
    5 s later. ::

        import collections
        import threading

        pump_cmd_q = collections.deque()
        abort_event = threading.Event()
        my_pumpcon = PumpCommThread(pump_cmd_q, abort_event)
        my_pumpcon.start()

        init_cmd = ('connect', ('COM6', 'pump2', 'VICI_M50'),
            {'flow_cal': 626.2, 'backlash_cal': 9.278})
        flow_rate_cmd = ('set_flow_rate', ('pump2', 2000), {})
        start_cmd = ('start_flow', ('pump2',), {})
        stop_cmd = ('stop', ('pump2',), {})

        pump_cmd_q.append(init_cmd)
        pump_cmd_q.append(start_cmd)
        pump_cmd_q.append(flow_rate_cmd)
        time.sleep(5)
        pump_cmd_q.append(stop_cmd)

        my_pumpcon.stop()
    """

    def __init__(self, command_queue, answer_queue, abort_event, motor=None,
        name=None):
        """
        Initializes the custom thread. Important parameters here are the
        list of known commands ``_commands`` and known pumps ``known_pumps``.

        :param collections.deque command_queue: The queue used to pass commands to
            the thread.

        :param threading.Event abort_event: An event that is set when the thread
            needs to abort, and otherwise is not set.
        """
        threading.Thread.__init__(self, name=name)
        self.daemon = True

        logger.info("Starting motor control thread: %s", self.name)

        self.command_queue = command_queue
        self.answer_queue = answer_queue
        self._abort_event = abort_event
        self._stop_event = threading.Event()

        self._commands = {'connect'     : self._connect_motor,
            'disconnect'                : self._disconnect_motor,
            'add_motor'                 : self._add_motor,
            'controller_status'         : self._get_controller_status,
            'group_status'              : self._get_group_status,
            'get_position'              : self._get_position,
            'set_position'              : self._set_position,
            'get_positioner_position'   : self._get_positioner_position,
            'set_positioner_position'   : self._set_positioner_position,
            'get_units'                 : self._get_units,
            'set_units'                 : self._set_units,
            'is_moving'                 : self._is_moving,
            'positioner_is_moving'      : self._positioner_is_moving,
            'move_relative'             : self._move_relative,
            'move_absolute'             : self._move_absolute,
            'move_positioner_relative'  : self._move_positioner_relative,
            'move_positioner_absolute'  : self._move_positioner_absolute,
            'home'                      : self._home,
            'home_positioner'           : self._home_positioner,
            'get_high_limit'            : self._get_high_limit,
            'get_low_limit'             : self._get_low_limit,
            'set_high_limit'            : self._set_high_limit,
            'set_low_limit'             : self._set_low_limit,
            'set_limits'                : self._set_limits,
            'get_limits'                : self._get_limits,
            'get_velocity'              : self._get_velocity,
            'set_velocity'              : self._set_velocity,
            'get_acceleration'          : self._get_acceleration,
            'set_acceleration'          : self._set_acceleration,
            'get_position_compare'      : self._get_position_compare,
            'set_position_compare'      : self._set_position_compare,
            'start_position_compare'    : self._start_position_compare,
            'stop_position_compare'     : self._stop_position_compare,
            'get_position_compare_pulse': self._get_position_compare_pulse,
            'set_position_compare_pulse': self._set_position_compare_pulse,
            }

        self._connected_motors = OrderedDict()

        if motor is not None:
            self._connected_motors[motor[0]] = motor[1]

        self.known_motors = {'Newport_XPS' : NewportXPSMotor,
            'Zaber' : ZaberMotor,
            }

    def run(self):
        """
        Custom run method for the thread.
        """
        while True:
            if len(self.command_queue) > 0:
                logger.debug("Getting new command")
                command, args, kwargs = self.command_queue.popleft()
            else:
                command = None

            if self._abort_event.is_set():
                logger.debug("Abort event detected")
                self._abort()
                command = None

            if self._stop_event.is_set():
                logger.debug("Stop event detected")
                self._abort()
                break

            if command is not None:
                logger.debug("Processing cmd '%s' with args: %s and kwargs: %s ", command, ', '.join(['{}'.format(a) for a in args]), ', '.join(['{}:{}'.format(kw, item) for kw, item in kwargs.items()]))
                try:
                    self._commands[command](*args, **kwargs)
                except Exception:
                    msg = ("Motor control thread failed to run command '%s' "
                        "with args: %s and kwargs: %s " %(command,
                        ', '.join(['{}'.format(a) for a in args]),
                        ', '.join(['{}:{}'.format(kw, item) for kw, item in kwargs.items()])))
                    logger.exception(msg)

                    if command == 'connect' or command == 'disconnect':
                        self.answer_queue.append(False)
            else:
                time.sleep(.01)

        if self._stop_event.is_set():
            self._stop_event.clear()
        else:
            self._abort()
        logger.info("Quitting motor control thread: %s", self.name)

    def _connect_motor(self, device, name, motor_type, **kwargs):
        """
        This method connects to a pump by creating a new :py:class:`Pump` subclass
        object (e.g. a new :py:class:`M50Pump` object). This pump is saved in the thread
        and can be called later to do stuff. All pumps must be connected before
        they can be used.

        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str

        :param motor_type: A pump type in the ``known_motors`` dictionary.
        :type motor_type: str

        :param \*\*kwargs: This function accepts arbitrary keyword args that are passed
            directly to the :py:class:`Pump` subclass that is called. For example,
            for an :py:class:`M50Pump` you could pass ``flow_cal`` and ``backlash``.
        """
        logger.info("Connecting motor %s", name)
        new_motor = self.known_motors[motor_type](device, name, **kwargs)
        self._connected_motors[name] = new_motor
        self.answer_queue.append(True)
        logger.debug("Motor %s connected", name)

    def _disconnect_motor(self, name, **kwargs):
        """
        This method sets the flow rate for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float flow_rate: The flow rate for the pump.
        """
        logger.info("Disconnecting motor %s", name)
        motor = self._connected_motors[name]
        motor.disconnect()
        del self._connected_motors[name]
        self.answer_queue.append(True)
        logger.debug("Motor %s disconnected", name)

    def _add_motor(self, motor, name, **kwargs):
        logger.info('Adding motor %s', name)
        self._connected_motors[name] = motor
        self.answer_queue.append(True)
        logger.debug('Motor %s added', name)

    def _get_controller_status(self, name, **kwargs):
        """
        This method sets the flow rate for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float flow_rate: The flow rate for the pump.
        """
        logger.info("Getting motor %s controller status", name)
        motor = self._connected_motors[name]
        status, descrip = motor.get_controller_status()
        self.answer_queue.append((status, descrip))
        logger.debug("Got motor %s controller status", name)

    def _get_group_status(self, name, **kwargs):
        """
        This method sets the flow rate for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float flow_rate: The flow rate for the pump.
        """
        logger.info("Getting motor %s group status", name)
        motor = self._connected_motors[name]
        status, descrip = motor.get_group_status()
        self.answer_queue.append((status, descrip))
        logger.debug("Got motor %s group status", name)

    def _get_position(self, name, **kwargs):
        """
        This method sets the flow rate for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float flow_rate: The flow rate for the pump.
        """
        logger.info("Getting motor %s position(s)", name)
        motor = self._connected_motors[name]
        position = motor.position
        self.answer_queue.append(position)
        logger.debug("Got motor %s positions", name)

    def _set_position(self, name, positions, **kwargs):
        """
        This method sets the flow rate for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float flow_rate: The flow rate for the pump.
        """
        logger.info("Setting motor %s positions", name)
        motor = self._connected_motors[name]
        motor.position = positions
        logger.debug("Motor %s positions set", name)

    def _get_positioner_position(self, name, positioner, index, **kwargs):
        """
        This method sets the flow rate for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float flow_rate: The flow rate for the pump.
        """
        logger.info("Getting motor %s positioner %s position(s)", name, positioner)
        motor = self._connected_motors[name]
        position = motor.get_positioner_position(positioner, index)
        self.answer_queue.append(position)
        logger.debug("Got motor %s positioner %s positions", name, positioner)

    def _set_positioner_position(self, name, positioner, index, positions, **kwargs):
        """
        This method sets the flow rate for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float flow_rate: The flow rate for the pump.
        """
        logger.info("Setting motor %s %s positions", name)
        motor = self._connected_motors[name]
        motor.set_positioner_position(positioner, index, positions)
        logger.debug("Motor %s positions set", name)

    def _get_units(self, name, **kwargs):
        """
        This method sets the flow rate for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float flow_rate: The flow rate for the pump.
        """
        logger.info("Getting motor %s units", name)
        motor = self._connected_motors[name]
        units = motor.units
        self.answer_queue.append(units)
        logger.debug("Got motor %s units", name)

    def _set_units(self, name, units, **kwargs):
        """
        This method sets the flow rate for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float flow_rate: The flow rate for the pump.
        """
        logger.info("Setting motor %s units", name)
        motor = self._connected_motors[name]
        motor.units = units
        logger.debug("Motor %s units set", name)

    def _is_moving(self, name, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        logger.info("Checking if motor %s is moving", name)
        motor = self._connected_motors[name]
        is_moving = motor.is_moving()
        self.answer_queue.append(is_moving)
        logger.debug("Motor %s is moving: %s", name, str(is_moving))

    def _positioner_is_moving(self, name, positioner, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        logger.info("Checking if motor %s is moving", name)
        motor = self._connected_motors[name]
        is_moving = motor.positioner_is_moving()
        self.answer_queue.append(is_moving)
        logger.debug("Motor %s is moving: %s", name, str(is_moving))

    def _move_relative(self, name, displacements, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        logger.info("Moving motor %s relative", name)
        motor = self._connected_motors[name]
        success = motor.move_relative(displacements)
        self.answer_queue.append(success)
        logger.debug("Motor %s moved relative", name)

    def _move_absolute(self, name, positions, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        logger.info("Moving motor %s absolute", name)
        motor = self._connected_motors[name]
        success = motor.move_absolute(positions)
        self.answer_queue.append(success)
        logger.debug("Motor %s moved absolute", name)

    def _move_positioner_relative(self, name, positioner, index, displacements, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        logger.info("Moving motor %s positioner %s relative", name, positioner)
        motor = self._connected_motors[name]
        success = motor.move_positioner_relative(positioner, index, displacements)
        self.answer_queue.append(success)
        logger.debug("Motor %s positioner %s moved relative", name, positioner)

    def _move_positioner_absolute(self, name, positioner, index, positions, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        logger.info("Moving motor %s positioner %s absolute", name, positioner)
        motor = self._connected_motors[name]
        success = motor.move_positioner_absolute(positioner, index, positions)
        self.answer_queue.append(success)
        logger.debug("Motor %s positioner %s moved absolute", name, positioner)

    def _home(self, name, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        logger.info("Homing motor %s", name)
        motor = self._connected_motors[name]
        success = motor.home()
        self.answer_queue.append(success)
        logger.debug("Motor %s homed", name)

    def _home_positioner(self, name, positioner, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        logger.info("Homing motor %s positioner %s", name, positioner)
        motor = self._connected_motors[name]
        success = motor.home_positioner(positioner)
        self.answer_queue.append(success)
        logger.debug("Motor %s positioner %s homed", name, positioner)

    def _get_high_limit(self, name, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        if 'positioner' in kwargs:
            logger.info("Getting motor %s positioner %s high limit", name, kwargs['positioner'])
        else:
            logger.info("Getting motor %s high limit", name)
        motor = self._connected_motors[name]
        limit = motor.get_high_limit(**kwargs)
        self.answer_queue.append(limit)
        if 'positioner' in kwargs:
            logger.debug("Got motor %s positioner %s high limit", name, kwargs['positioner'])
        else:
            logger.debug("Got motor %s high limit", name)

    def _get_low_limit(self, name, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        if 'positioner' in kwargs:
            logger.info("Getting motor %s positioner %s low limit", name, kwargs['positioner'])
        else:
            logger.info("Getting motor %s low limit", name)
        motor = self._connected_motors[name]
        limit = motor.get_low_limit(**kwargs)
        self.answer_queue.append(limit)
        if 'positioner' in kwargs:
            logger.debug("Got motor %s positioner %s low limit", name, kwargs['positioner'])
        else:
            logger.debug("Got motor %s low limit", name)

    def _get_limits(self, name, **kwargs):
        if 'positioner' in kwargs:
            logger.info("Getting motor %s positioner %s limits", name, kwargs['positioner'])
        else:
            logger.info("Getting motor %s limits", name)
        motor = self._connected_motors[name]
        limit = motor.get_limits(**kwargs)
        self.answer_queue.append(limit)
        if 'positioner' in kwargs:
            logger.debug("Got motor %s positioner %s limits", name, kwargs['positioner'])
        else:
            logger.debug("Got motor %s limits", name)

    def _set_high_limit(self, name, limit, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        if 'positioner' in kwargs:
            logger.info("Setting motor %s positioner %s high limit", name, kwargs['positioner'])
        else:
            logger.info("Setting motor %s high limit", name)
        motor = self._connected_motors[name]
        success = motor.set_high_limit(limit, **kwargs)
        self.answer_queue.append(success)
        if 'positioner' in kwargs:
            logger.debug("Set motor %s positioner %s high limit", name, kwargs['positioner'])
        else:
            logger.debug("Set motor %s positioner %s high limit", name)

    def _set_low_limit(self, name, limit, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        if 'positioner' in kwargs:
            logger.info("Setting motor %s positioner %s low limit", name, kwargs['positioner'])
        else:
            logger.info("Setting motor %s low limit", name)
        motor = self._connected_motors[name]
        success = motor.set_low_limit(limit, **kwargs)
        self.answer_queue.append(success)
        if 'positioner' in kwargs:
            logger.debug("Set motor %s positioner %s low limit", name, kwargs['positioner'])
        else:
            logger.debug("Set motor %s low limit", name)

    def _set_limits(self, name, low_limit, high_limit, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        if 'positioner' in kwargs:
            logger.info("Setting motor %s positioner %s limits", name, kwargs['positioner'])
        else:
            logger.info("Setting motor %s limits", name)
        motor = self._connected_motors[name]
        success = motor.set_limits(low_limit, high_limit, **kwargs)
        self.answer_queue.append(success)
        if 'positioner' in kwargs:
            logger.debug("Set motor %s positioner %s limits", name, kwargs['positioner'])
        else:
            logger.debug("Set motor %s limits", name)

    def _get_velocity(self, name, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        if 'positioner' in kwargs:
            logger.info("Getting motor %s positioner %s velocity", name, kwargs['positioner'])
        else:
            logger.info("Getting motor %s velocity", name)
        motor = self._connected_motors[name]
        velocity = motor.get_velocity(**kwargs)
        self.answer_queue.append(velocity)
        if 'positioner' in kwargs:
            logger.debug("Got motor %s positioner %s velocity", name, kwargs['positioner'])
        else:
            logger.debug("Got motor %s velocity", name)

    def _set_velocity(self, name, velocity, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        if 'positioner' in kwargs:
            logger.info("Setting motor %s positioner %s velocity", name, kwargs['positioner'])
        else:
            logger.info("Setting motor %s velocity", name)
        motor = self._connected_motors[name]
        success = motor.set_velocity(velocity, **kwargs)
        self.answer_queue.append(success)
        if 'positioner' in kwargs:
            logger.debug("Set motor %s positioner %s velocity", name, kwargs['positioner'])
        else:
            logger.debug("Set motor %s velocity", name)

    def _get_acceleration(self, name, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        if 'positioner' in kwargs:
            logger.info("Getting motor %s positioner %s acceleration", name, kwargs['positioner'])
        else:
            logger.info("Getting motor %s acceleration", name)
        motor = self._connected_motors[name]
        acceleration = motor.get_acceleration(**kwargs)
        self.answer_queue.append(acceleration)
        if 'positioner' in kwargs:
            logger.debug("Got motor %s positioner %s acceleration", name, kwargs['positioner'])
        else:
            logger.debug("Got motor %s positioner", name)

    def _set_acceleration(self, name, acceleration, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        if 'positioner' in kwargs:
            logger.info("Setting motor %s positioner %s acceleration", name, kwargs['positioner'])
        else:
            logger.info("Setting motor %s acceleration", name)
        motor = self._connected_motors[name]
        success = motor.set_acceleration(acceleration, **kwargs)
        self.answer_queue.append(success)
        if 'positioner' in kwargs:
            logger.debug("Set motor %s positioner %s acceleration", name, kwargs['positioner'])
        else:
            logger.debug("Set motor %s acceleration", name)

    def _get_position_compare(self, name, positioner, index, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        logger.info("Getting motor %s positioner %s position compare settings", \
            name, positioner)
        motor = self._connected_motors[name]
        min_pos, max_pos, step, enable = motor.get_position_compare(positioner, index)
        self.answer_queue.append((min_pos, max_pos, step, enable))
        logger.debug("Got motor %s positioner %s position compare settings", \
            name, positioner)

    def _set_position_compare(self, name, positioner, index, min_pos, max_pos,
        step, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        logger.info("Setting motor %s positioner %s position compare settings", \
            name, positioner)
        motor = self._connected_motors[name]
        success = motor.set_position_compare(positioner, index, min_pos, max_pos,
            step)
        self.answer_queue.append(success)
        logger.debug("Set motor %s positioner %s position compare settings", \
            name, positioner)

    def _start_position_compare(self, name, positioner, index, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        logger.info("Starting motor %s positioner %s position compare", \
            name, positioner)
        motor = self._connected_motors[name]
        success = motor.start_position_compare(positioner, index)
        self.answer_queue.append(success)
        logger.debug("Started motor %s positioner %s position compare", \
            name, positioner)

    def _stop_position_compare(self, name, positioner, index, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        logger.info("Stopping motor %s positioner %s position compare", \
            name, positioner)
        motor = self._connected_motors[name]
        success = motor.stop_position_compare(positioner, index)
        self.answer_queue.append(success)
        logger.debug("Stopped motor %s positioner %s position compare", \
            name, positioner)

    def _get_position_compare_pulse(self, name, positioner, index, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        logger.info("Getting motor %s positioner %s position compare pulse \
         settings", name, positioner)
        motor = self._connected_motors[name]
        pulse_width, encoder_settle_time = motor.get_position_compare_pulse(positioner, index)
        self.answer_queue.append((pulse_width, encoder_settle_time))
        logger.debug("Got motor %s positioner %s position compare pulse \
            settings", name, positioner)

    def _set_position_compare_pulse(self, name, positioner, index, pulse_width,
        encoder_settle_time, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        logger.info("Setting motor %s positioner %s position compare pulse \
            settings", name, positioner)
        motor = self._connected_motors[name]
        success = motor.set_position_compare(positioner, index, pulse_width,
            encoder_settle_time)
        self.answer_queue.append(success)
        logger.debug("Set motor %s positioner %s position compare pulse \
            settings", name, positioner)

    def _abort(self):
        """Clears the ``command_queue`` and aborts all current motor motions."""
        logger.info("Aborting motor control thread %s current and future commands", self.name)
        self.command_queue.clear()

        for name, motor in self._connected_motors.items():
            try:
                motor.stop()
            except Exception:
                pass

        self._abort_event.clear()
        logger.debug("Motor control thread %s aborted", self.name)

    def stop(self):
        """Stops the thread cleanly."""
        logger.info("Starting to clean up and shut down motor control thread: %s", self.name)
        self._stop_event.set()

class MotorPanel(wx.Panel):
    """
    This pump panel supports standard flow controls and settings, including
    connection settings, for a pump. It is meant to be embedded in a larger application
    and can be instanced several times, once for each pump. It communciates
    with the pumps using the :py:class:`PumpCommThread`. Currently it only supports
    the :py:class:`M50Pump`, but it should be easy to extend for other pumps. The
    only things that should have to be changed are the are adding in pump-specific
    settings, modeled after how the ``m50_pump_sizer`` is constructed in the
    :py:func:`_create_layout` function, and then add in type switching in the
    :py:func:`_on_type` function.
    """
    def __init__(self, parent, panel_id, panel_name, motor_cmd_q, motor_answer_q,
        known_motors, motor_name, ports, motor_type=None, motor_args=[], motor_kwargs={}):
        """
        Initializes the custom thread. Important parameters here are the
        list of known commands ``_commands`` and known pumps ``known_pumps``.

        :param wx.Window parent: Parent class for the panel.

        :param int panel_id: wx ID for the panel.

        :param str panel_name: Name for the panel

        :param list all_comports: A list containing all comports that the pump
            could be connected to.

        :param collections.deque pump_cmd_q: The ``pump_cmd_q`` that was passed to
            the :py:class:`PumpCommThread`.

        :param list known_pumps: The list of known pump types, obtained from
            the :py:class:`PumpCommThread`.

        :param str pump_name: An identifier for the pump, displayed in the pump
            panel.

        :param str pump_type: One of the ``known_pumps``, corresponding to the pump
            connected to this panel. Only required if you are connecting the pump
            when the panel is first set up (rather than manually later).

        :param str comport: The comport the pump is connected to. Only required
            if you are connecting the pump when the panel is first set up (rather
            than manually later).

        :param list pump_args: Pump specific arguments for initialization.
            Only required if you are connecting the pump when the panel is first
            set up (rather than manually later).

        :param dict pump_kwargs: Pump specific keyword arguments for initialization.
            Only required if you are connecting the pump when the panel is first
            set up (rather than manually later).

        """

        wx.Panel.__init__(self, parent, panel_id, name=panel_name)
        logger.debug('Initializing MotorPanel for motor %s', motor_name)

        self.name = motor_name
        self.motor_cmd_q = motor_cmd_q
        self.known_motors = known_motors
        self.answer_q = motor_answer_q
        self.connected = False
        self.ports = ports

        self.monitor_event = threading.Event()
        self.monitor_thread = threading.Thread(target=self._update_status)
        self.monitor_thread.daemon = True

        self.answer_event = threading.Event()
        self.answer_type_q = queue.Queue()
        self.answer_thread = threading.Thread(target=self._get_answer)
        self.answer_thread.daemon = True
        self.answer_thread.start()

        self._create_layout()

        self._initmotor(motor_type, motor_args, motor_kwargs)


    def _create_layout(self):
        """Creates the layout for the panel."""

        self.status = wx.StaticText(self, label='Not connected')
        self.moving = wx.StaticText(self, label='False')

        status_grid = wx.FlexGridSizer(rows=3, cols=2, vgap=2, hgap=2)
        status_grid.AddGrowableCol(1)
        status_grid.Add(wx.StaticText(self, label='Motor name:'))
        status_grid.Add(wx.StaticText(self, label=self.name), 1, wx.EXPAND)
        status_grid.Add(wx.StaticText(self, label='Status:'))
        status_grid.Add(self.status, 1, wx.EXPAND)
        status_grid.Add(wx.StaticText(self, label='Motor moving:'))
        status_grid.Add(self.moving, 1, wx.EXPAND)

        status_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Info'),
            wx.VERTICAL)
        status_sizer.Add(status_grid, 1, flag=wx.EXPAND|wx.ALL, border=5)


        self.low_limit = wx.TextCtrl(self, size=(60,-1), style=wx.TE_PROCESS_ENTER,
            validator=utils.CharValidator('float_te'))
        self.pos = wx.StaticText(self)
        self.pos.SetForegroundColour('blue')
        self.high_limit = wx.TextCtrl(self, size=(60,-1), style=wx.TE_PROCESS_ENTER,
            validator=utils.CharValidator('float_te'))

        self.low_limit.Bind(wx.EVT_TEXT, self._on_limit_text)
        self.low_limit.Bind(wx.EVT_TEXT_ENTER, self._on_low_limit)
        self.high_limit.Bind(wx.EVT_TEXT, self._on_limit_text)
        self.high_limit.Bind(wx.EVT_TEXT_ENTER, self._on_high_limit)

        self.pos_sizer = wx.FlexGridSizer(rows=2, cols=3, vgap=2, hgap=2)
        self.pos_sizer.Add(wx.StaticText(self, label='Low lim.'),
            flag=wx.ALIGN_CENTER_HORIZONTAL)
        self.pos_sizer.Add(wx.StaticText(self, label='Current Pos.'),
            flag=wx.ALIGN_CENTER_HORIZONTAL)
        self.pos_sizer.Add(wx.StaticText(self, label='High lim.'),
            flag=wx.ALIGN_CENTER_HORIZONTAL)
        self.pos_sizer.Add(self.low_limit,
            flag=wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_CENTER_VERTICAL)
        self.pos_sizer.Add(self.pos,
            flag=wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_CENTER_VERTICAL)
        self.pos_sizer.Add(self.high_limit,
            flag=wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_CENTER_VERTICAL)
        self.pos_sizer.AddGrowableCol(0)
        self.pos_sizer.AddGrowableCol(1)
        self.pos_sizer.AddGrowableCol(2)


        self.pos_ctrl = wx.TextCtrl(self, size=(50,-1),
            validator=utils.CharValidator('float_neg'))
        move_btn = buttons.ThemedGenButton(self, label='Move', style=wx.BU_EXACTFIT)
        set_btn = buttons.ThemedGenButton(self, label='Set', style=wx.BU_EXACTFIT)
        move_btn.Bind(wx.EVT_BUTTON, self._on_move)
        set_btn.Bind(wx.EVT_BUTTON, self._on_set)

        mabs_sizer = wx.BoxSizer(wx.HORIZONTAL)
        mabs_sizer.Add(wx.StaticText(self, label='Position:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        mabs_sizer.Add(self.pos_ctrl, 1, border=2,
            flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)
        mabs_sizer.Add(move_btn, border=2, flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)
        mabs_sizer.Add(set_btn, border=2, flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)


        self.mrel_ctrl = wx.TextCtrl(self, value='1.0', size=(50, -1),
            validator=utils.CharValidator('float'))
        tp_btn = buttons.ThemedGenButton(self, label='+ >', style=wx.BU_EXACTFIT)
        tm_btn = buttons.ThemedGenButton(self, label='< -', style=wx.BU_EXACTFIT)
        tp_btn.Bind(wx.EVT_BUTTON, self._on_mrel_plus)
        tm_btn.Bind(wx.EVT_BUTTON, self._on_mrel_minus)


        mrel_sizer = wx.BoxSizer(wx.HORIZONTAL)
        mrel_sizer.Add(wx.StaticText(self, label='Rel. move:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        mrel_sizer.Add(tm_btn, border=2, flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)
        mrel_sizer.Add(self.mrel_ctrl, 1, border=2,
            flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)
        mrel_sizer.Add(tp_btn, border=2, flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)


        self.v_ctrl = wx.TextCtrl(self, size=(50, -1), style=wx.TE_PROCESS_ENTER,
            validator=utils.CharValidator('float_te'))
        self.a_ctrl = wx.TextCtrl(self, size=(50, -1), style=wx.TE_PROCESS_ENTER,
            validator=utils.CharValidator('float_te'))
        self.v_ctrl.Bind(wx.EVT_TEXT, self._on_limit_text)
        self.v_ctrl.Bind(wx.EVT_TEXT_ENTER, self._on_v)
        self.a_ctrl.Bind(wx.EVT_TEXT, self._on_limit_text)
        self.a_ctrl.Bind(wx.EVT_TEXT_ENTER, self._on_a)

        va_sizer = wx.FlexGridSizer(rows=2, cols=2, vgap=2, hgap=2)
        va_sizer.Add(wx.StaticText(self, label='Velocity:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        va_sizer.Add(self.v_ctrl, flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        va_sizer.Add(wx.StaticText(self, label='Acceleration:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        va_sizer.Add(self.a_ctrl, flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        va_sizer.AddGrowableCol(1)


        home_btn = wx.Button(self, label='Home')
        stop_btn = wx.Button(self, label='Abort')

        home_btn.Bind(wx.EVT_BUTTON, self._on_home)
        stop_btn.Bind(wx.EVT_BUTTON, self._on_stop)

        ctrl_btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        ctrl_btn_sizer.Add(home_btn, flag=wx.ALIGN_LEFT)
        ctrl_btn_sizer.AddStretchSpacer(1)
        ctrl_btn_sizer.Add(stop_btn, flag=wx.ALIGN_RIGHT)

        self.mname = wx.StaticText(self)
        self.mname.SetForegroundColour('Red')

        self.control_mtr1_sizer = wx.BoxSizer(wx.VERTICAL)
        self.control_mtr1_sizer.Add(self.mname)
        self.control_mtr1_sizer.Add(self.pos_sizer, border=5, flag=wx.TOP|wx.EXPAND)
        self.control_mtr1_sizer.Add(mabs_sizer, border=5, flag=wx.TOP|wx.EXPAND)
        self.control_mtr1_sizer.Add(mrel_sizer, border=5, flag=wx.TOP|wx.EXPAND)
        self.control_mtr1_sizer.Add(va_sizer, border=5, flag=wx.TOP|wx.BOTTOM|wx.EXPAND)
        self.control_mtr1_sizer.Add(wx.StaticLine(self), border=10,
            flag=wx.EXPAND|wx.LEFT|wx.RIGHT)
        self.control_mtr1_sizer.Add(ctrl_btn_sizer, border=5, flag=wx.TOP|wx.EXPAND)


        # Second motor control for two axis motor groups

        self.low_limit2 = wx.TextCtrl(self, size=(60,-1), style=wx.TE_PROCESS_ENTER,
            validator=utils.CharValidator('float_te'))
        self.pos2 = wx.StaticText(self)
        self.pos2.SetForegroundColour('blue')
        self.high_limit2 = wx.TextCtrl(self, size=(60,-1), style=wx.TE_PROCESS_ENTER,
            validator=utils.CharValidator('float_te'))

        self.low_limit2.Bind(wx.EVT_TEXT, self._on_limit_text)
        self.low_limit2.Bind(wx.EVT_TEXT_ENTER, self._on_low_limit2)
        self.high_limit2.Bind(wx.EVT_TEXT, self._on_limit_text)
        self.high_limit2.Bind(wx.EVT_TEXT_ENTER, self._on_high_limit2)

        self.pos_sizer2 = wx.FlexGridSizer(rows=2, cols=3, vgap=2, hgap=2)
        self.pos_sizer2.Add(wx.StaticText(self, label='Low lim.'),
            flag=wx.ALIGN_CENTER_HORIZONTAL)
        self.pos_sizer2.Add(wx.StaticText(self, label='Current Pos.'),
            flag=wx.ALIGN_CENTER_HORIZONTAL)
        self.pos_sizer2.Add(wx.StaticText(self, label='High lim.'),
            flag=wx.ALIGN_CENTER_HORIZONTAL)
        self.pos_sizer2.Add(self.low_limit2,
            flag=wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_CENTER_VERTICAL)
        self.pos_sizer2.Add(self.pos2,
            flag=wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_CENTER_VERTICAL)
        self.pos_sizer2.Add(self.high_limit2,
            flag=wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_CENTER_VERTICAL)
        self.pos_sizer2.AddGrowableCol(0)
        self.pos_sizer2.AddGrowableCol(1)
        self.pos_sizer2.AddGrowableCol(2)


        self.pos_ctrl2 = wx.TextCtrl(self, size=(50,-1),
            validator=utils.CharValidator('float_neg'))
        move_btn2 = buttons.ThemedGenButton(self, label='Move', style=wx.BU_EXACTFIT)
        set_btn2 = buttons.ThemedGenButton(self, label='Set', style=wx.BU_EXACTFIT)
        move_btn2.Bind(wx.EVT_BUTTON, self._on_move2)
        set_btn2.Bind(wx.EVT_BUTTON, self._on_set2)

        mabs_sizer2 = wx.BoxSizer(wx.HORIZONTAL)
        mabs_sizer2.Add(wx.StaticText(self, label='Position:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        mabs_sizer2.Add(self.pos_ctrl2, 1, border=2,
            flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)
        mabs_sizer2.Add(move_btn2, border=2, flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)
        mabs_sizer2.Add(set_btn2, border=2, flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)


        self.mrel_ctrl2 = wx.TextCtrl(self, value='1.0', size=(50, -1),
            validator=utils.CharValidator('float'))
        tp_btn2 = buttons.ThemedGenButton(self, label='+ >', style=wx.BU_EXACTFIT)
        tm_btn2 = buttons.ThemedGenButton(self, label='< -', style=wx.BU_EXACTFIT)
        tp_btn2.Bind(wx.EVT_BUTTON, self._on_mrel_plus2)
        tm_btn2.Bind(wx.EVT_BUTTON, self._on_mrel_minus2)


        mrel_sizer2 = wx.BoxSizer(wx.HORIZONTAL)
        mrel_sizer2.Add(wx.StaticText(self, label='Rel. move:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        mrel_sizer2.Add(tm_btn2, border=2, flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)
        mrel_sizer2.Add(self.mrel_ctrl2, 1, border=2,
            flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)
        mrel_sizer2.Add(tp_btn2, border=2, flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)


        self.v_ctrl2 = wx.TextCtrl(self, size=(50, -1), style=wx.TE_PROCESS_ENTER,
            validator=utils.CharValidator('float_te'))
        self.a_ctrl2 = wx.TextCtrl(self, size=(50, -1), style=wx.TE_PROCESS_ENTER,
            validator=utils.CharValidator('float_te'))
        self.v_ctrl2.Bind(wx.EVT_TEXT, self._on_limit_text)
        self.v_ctrl2.Bind(wx.EVT_TEXT_ENTER, self._on_v2)
        self.a_ctrl2.Bind(wx.EVT_TEXT, self._on_limit_text)
        self.a_ctrl2.Bind(wx.EVT_TEXT_ENTER, self._on_a2)


        va_sizer2 = wx.FlexGridSizer(rows=2, cols=2, vgap=2, hgap=2)
        va_sizer2.Add(wx.StaticText(self, label='Velocity:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        va_sizer2.Add(self.v_ctrl2, flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        va_sizer2.Add(wx.StaticText(self, label='Acceleration:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        va_sizer2.Add(self.a_ctrl2, flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        va_sizer2.AddGrowableCol(1)


        home_btn2 = wx.Button(self, label='Home')
        stop_btn2 = wx.Button(self, label='Abort')

        home_btn2.Bind(wx.EVT_BUTTON, self._on_home2)
        stop_btn2.Bind(wx.EVT_BUTTON, self._on_stop2)

        ctrl_btn_sizer2 = wx.BoxSizer(wx.HORIZONTAL)
        ctrl_btn_sizer2.Add(home_btn2, flag=wx.ALIGN_LEFT)
        ctrl_btn_sizer2.AddStretchSpacer(1)
        ctrl_btn_sizer2.Add(stop_btn2, flag=wx.ALIGN_RIGHT)

        self.mname2 = wx.StaticText(self)
        self.mname2.SetForegroundColour('Red')

        self.control_mtr2_sizer = wx.BoxSizer(wx.VERTICAL)
        self.control_mtr2_sizer.Add(self.mname2)
        self.control_mtr2_sizer.Add(self.pos_sizer2, border=5, flag=wx.TOP|wx.EXPAND)
        self.control_mtr2_sizer.Add(mabs_sizer2, border=5, flag=wx.TOP|wx.EXPAND)
        self.control_mtr2_sizer.Add(mrel_sizer2, border=5, flag=wx.TOP|wx.EXPAND)
        self.control_mtr2_sizer.Add(va_sizer2, border=5, flag=wx.TOP|wx.BOTTOM|wx.EXPAND)
        self.control_mtr2_sizer.Add(wx.StaticLine(self), border=10,
            flag=wx.EXPAND|wx.LEFT|wx.RIGHT)
        self.control_mtr2_sizer.Add(ctrl_btn_sizer2, border=5, flag=wx.TOP|wx.EXPAND)

        self.control_sub_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.control_sub_sizer.Add(self.control_mtr1_sizer, proportion=1,
            border=10, flag=wx.RIGHT)
        self.control_sub_sizer.Add(self.control_mtr2_sizer, proportion=1,
            border=10, flag=wx.LEFT)

        group_move = wx.Button(self, label='Group Move')
        group_move.Bind(wx.EVT_BUTTON, self._on_group_move)
        group_abort = wx.Button(self, label='Abort Group')
        group_abort.Bind(wx.EVT_BUTTON, self._on_group_abort)

        self.group_btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.group_btn_sizer.Add(group_move, border=5,
            flag=wx.ALIGN_CENTER_HORIZONTAL|wx.RIGHT)
        self.group_btn_sizer.Add(group_abort, border=5,
            flag=wx.ALIGN_CENTER_HORIZONTAL|wx.LEFT)

        self.control_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Controls'),
            wx.VERTICAL)
        self.control_sizer.Add(self.control_sub_sizer, border=5,
            flag=wx.LEFT|wx.RIGHT|wx.TOP)
        self.control_sizer.Add(self.group_btn_sizer, border=20,
            flag=wx.ALIGN_CENTER_HORIZONTAL|wx.TOP)
        self.control_sizer.AddSpacer(5)


        types = [key.replace('_', ' ') for key in self.known_motors.keys()]
        self.type_ctrl = wx.Choice(self, choices=types)
        self.type_ctrl.Bind(wx.EVT_CHOICE, self._on_type)
        self.type_ctrl.SetSelection(0)
        type_sizer = wx.BoxSizer(wx.HORIZONTAL)
        type_sizer.Add(wx.StaticText(self, label='Motor type:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        type_sizer.Add(self.type_ctrl, border=2,
            flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)

        self.np_ip = wx.TextCtrl(self, size=(60,-1))
        self.np_port = wx.TextCtrl(self, size=(60,-1))
        self.np_group_name = wx.TextCtrl(self, size=(60,-1))
        self.np_pos1_name = wx.TextCtrl(self, size=(60,-1))
        self.np_pos2_name = wx.TextCtrl(self, size=(60,-1))
        self.np_group_type = wx.Choice(self, choices=['Single', 'XY'])
        self.np_pos2_label = wx.StaticText(self, label='Positioner 2:')
        self.np_group_type.SetSelection(0)
        self.np_group_type.Bind(wx.EVT_CHOICE, self._on_np_group_type)

        self.newport_xps_sizer = wx.FlexGridSizer(cols=2, rows=6, vgap=2, hgap=2)
        self.newport_xps_sizer.Add(wx.StaticText(self, label='IP Address:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.newport_xps_sizer.Add(self.np_ip,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        self.newport_xps_sizer.Add(wx.StaticText(self, label='Port:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.newport_xps_sizer.Add(self.np_port,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        self.newport_xps_sizer.Add(wx.StaticText(self, label='Group:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.newport_xps_sizer.Add(self.np_group_name,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        self.newport_xps_sizer.Add(wx.StaticText(self, label='Group type:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.newport_xps_sizer.Add(self.np_group_type,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        self.newport_xps_sizer.Add(wx.StaticText(self, label='Positioner 1:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.newport_xps_sizer.Add(self.np_pos1_name,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        self.newport_xps_sizer.Add(self.np_pos2_label,
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.newport_xps_sizer.Add(self.np_pos2_name,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        self.newport_xps_sizer.AddGrowableCol(1)


        self.zaber_port = wx.Choice(self, choices=self.ports)
        self.zaber_number = wx.TextCtrl(self, size=(60,-1))
        self.zaber_travel = wx.TextCtrl(self, size=(60, -1))

        self.zaber_sizer = wx.FlexGridSizer(cols=2, vgap=2, hgap=2)
        self.zaber_sizer.Add(wx.StaticText(self, label='Port:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.zaber_sizer.Add(self.zaber_port,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        self.zaber_sizer.Add(wx.StaticText(self, label='Number:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.zaber_sizer.Add(self.zaber_number,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        self.zaber_sizer.Add(wx.StaticText(self, label='Travel:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.zaber_sizer.Add(self.zaber_travel,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)

        self.connect_button = wx.Button(self, label='Connect')
        self.connect_button.Bind(wx.EVT_BUTTON, self._on_connect)

        self.settings_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Settings'),
            wx.VERTICAL)
        self.settings_sizer.Add(type_sizer, border=5, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.TOP)
        self.settings_sizer.Add(self.newport_xps_sizer, border=5,
            flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT)
        self.settings_sizer.Add(self.zaber_sizer, border=5,
            flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT)
        self.settings_sizer.Add(self.connect_button, border=5,
            flag=wx.ALIGN_CENTER_HORIZONTAL|wx.TOP|wx.BOTTOM)

        if self.type_ctrl.GetStringSelection() != 'Newport XPS':
            self.settings_sizer.Hide(self.newport_xps_sizer, recursive=True)
        if self.type_ctrl.GetStringSelection != 'Zaber':
            self.settings_sizer.Hide(self.zaber_sizer, recursive=True)

        if self.np_group_type.GetStringSelection() == 'Single':
            self.newport_xps_sizer.Hide(self.np_pos2_label)
            self.newport_xps_sizer.Hide(self.np_pos2_name)
            self.control_sub_sizer.Hide(self.control_mtr2_sizer, recursive=True)
            self.control_sizer.Hide(self.group_btn_sizer, recursive=True)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(status_sizer, flag=wx.EXPAND)
        top_sizer.Add(self.control_sizer, flag=wx.EXPAND)
        top_sizer.Add(self.settings_sizer, flag=wx.EXPAND)

        self.SetSizer(top_sizer)

    def _initmotor(self, motor_type, motor_args, motor_kwargs):
        """
        Initializes the motor parameters if any were provided. If enough are
        provided the motor is automatically connected.

        :param str motor_type: The motor type, corresponding to a ``known_motor``.

        :param str comport: The comport the motor is attached to.

        :param list motor_args: The motor positional initialization values.
            Appropriate values depend on the motor.

        :param dict motor_kwargs: The motor key word arguments. Appropriate
            values depend on the motor.
        """
        my_motors = [item.replace('_', ' ') for item in self.known_motors.keys()]
        if motor_type in my_motors:
            self.type_ctrl.SetStringSelection(motor_type)

        if motor_type == 'Newport XPS':
            self.np_group_name.SetValue(motor_args[0])
            self.np_ip.SetValue(motor_args[2])
            self.np_port.SetValue(motor_args[3])
            self.np_group_type.SetStringSelection(motor_args[4])
            self.np_pos1_name.SetValue(motor_args[5])
            self.np_pos2_name.SetValue(motor_args[6])

            if self.np_group_type.GetStringSelection() == 'XY':
                self.newport_xps_sizer.Show(self.np_pos2_label)
                self.newport_xps_sizer.Show(self.np_pos2_name)
                self.control_sub_sizer.Show(self.control_mtr2_sizer, recursive=True)
                self.control_sizer.Show(self.group_btn_sizer, recursive=True)

        if motor_type in my_motors:
            logger.info('Initialized motor %s on startup', self.name)
            self._connect()

    def _on_move(self, evt):
        pos = self.pos_ctrl.GetValue()
        if self.motor_params['type'] == 'Newport_XPS':
            mtr = self.motor_params['mtr1']
            index = 0
        else:
            mtr = None
            index = None
        self.move_abs(mtr, index, pos)

    def _on_set(self, evt):
        pos = self.pos_ctrl.GetValue()
        if self.motor_params['type'] == 'Newport_XPS':
            mtr = self.motor_params['mtr1']
            index = 0
        else:
            mtr = None
            index = None
        self.set_position(mtr, index, pos)

    def _on_mrel_plus(self, evt):
        pos = self.mrel_ctrl.GetValue()
        if self.motor_params['type'] == 'Newport_XPS':
            mtr = self.motor_params['mtr1']
            index = 0
        else:
            mtr = None
            index = None
        self.move_rel(mtr, index, pos, True)

    def _on_mrel_minus(self, evt):
        pos = self.mrel_ctrl.GetValue()
        if self.motor_params['type'] == 'Newport_XPS':
            mtr = self.motor_params['mtr1']
            index = 0
        else:
            mtr = None
            index = None
        self.move_rel(mtr, index, pos, False)

    def _on_limit_text(self, evt):
        evt_obj = evt.GetEventObject()
        evt_obj.SetBackgroundColour('yellow')

    def _on_low_limit(self, evt):
        evt_obj = evt.GetEventObject()
        evt_obj.SetBackgroundColour(wx.NullColour)
        lim = self.low_limit.GetValue()
        if self.motor_params['type'] == 'Newport_XPS':
            mtr = self.motor_params['mtr1']
            index = 0
        else:
            mtr = None
            index = None
        self.set_low_limit(mtr, index, lim)

    def _on_high_limit(self, evt):
        evt_obj = evt.GetEventObject()
        evt_obj.SetBackgroundColour(wx.NullColour)
        lim = self.high_limit.GetValue()
        if self.motor_params['type'] == 'Newport_XPS':
            mtr = self.motor_params['mtr1']
            index = 0
        else:
            mtr = None
            index = None
        self.set_high_limit(mtr, index, lim)

    def _on_v(self, evt):
        evt_obj = evt.GetEventObject()
        evt_obj.SetBackgroundColour(wx.NullColour)
        lim = self.v_ctrl.GetValue()
        if self.motor_params['type'] == 'Newport_XPS':
            mtr = self.motor_params['mtr1']
            index = 0
        else:
            mtr = None
            index = None
        self.set_v(mtr, index, lim)

    def _on_a(self, evt):
        evt_obj = evt.GetEventObject()
        evt_obj.SetBackgroundColour(wx.NullColour)
        lim = self.a_ctrl.GetValue()
        if self.motor_params['type'] == 'Newport_XPS':
            mtr = self.motor_params['mtr1']
            index = 0
        else:
            mtr = None
            index = None
        self.set_a(mtr, index, lim)

    def _on_home(self, evt):
        if self.motor_params['type'] == 'Newport_XPS':
            mtr = self.motor_params['mtr1']
            index = 0
        else:
            mtr = None
            index = None
        self.home(mtr)

    def _on_stop(self, evt):
        if self.motor_params['type'] == 'Newport_XPS':
            mtr = self.motor_params['mtr1']
            index = 0
            self.motor.stop_positioner(mtr)
        else:
            self.motor.stop()

    def _on_move2(self, evt):
        pos = self.pos_ctrl2.GetValue()
        mtr = self.motor_params['mtr2']
        index = 1
        self.move_abs(mtr, index, pos)

    def _on_set2(self, evt):
        pos = self.pos_ctrl2.GetValue()
        mtr = self.motor_params['mtr2']
        index = 1
        self.set_position(mtr, index, pos)

    def _on_mrel_plus2(self, evt):
        pos = self.mrel_ctrl2.GetValue()
        mtr = self.motor_params['mtr2']
        index = 1
        self.move_rel(mtr, index, pos, True)

    def _on_mrel_minus2(self, evt):
        pos = self.mrel_ctrl2.GetValue()
        mtr = self.motor_params['mtr2']
        index = 1
        self.move_rel(mtr, index, pos, False)

    def _on_low_limit2(self, evt):
        evt_obj = evt.GetEventObject()
        evt_obj.SetBackgroundColour(wx.NullColour)
        lim = self.low_limit2.GetValue()
        mtr = self.motor_params['mtr2']
        index = 1
        self.set_low_limit(mtr, index, lim)

    def _on_high_limit2(self, evt):
        evt_obj = evt.GetEventObject()
        evt_obj.SetBackgroundColour(wx.NullColour)
        lim = self.high_limit2.GetValue()
        mtr = self.motor_params['mtr2']
        index = 1
        self.set_high_limit(mtr, index, lim)

    def _on_v2(self, evt):
        evt_obj = evt.GetEventObject()
        evt_obj.SetBackgroundColour(wx.NullColour)
        lim = self.v_ctrl2.GetValue()
        mtr = self.motor_params['mtr2']
        index = 1
        self.set_v(mtr, index, lim)

    def _on_a2(self, evt):
        evt_obj = evt.GetEventObject()
        evt_obj.SetBackgroundColour(wx.NullColour)
        lim = self.a_ctrl2.GetValue()
        mtr = self.motor_params['mtr2']
        index = 1
        self.set_a(mtr, index, lim)

    def _on_home2(self, evt):
        mtr = self.motor_params['mtr2']
        self.home(mtr)

    def _on_stop2(self, evt):
        self.motor.stop_positioner(self.motor_params['mtr2'])

    def _on_group_move(self, evt):
        group = self.motor_params['group']
        pos = self.pos_ctrl.GetValue()
        pos2 = self.pos_ctrl2.GetValue()

        self.group_move_abs(group, [pos, pos2])

    def _on_group_abort(self, evt):
        self.motor.stop()

    def move_abs(self, mtr, index, pos):
        try:
            pos = float(pos)

            if self.motor_params['type'] == 'Newport_XPS':
                args = (self.name, mtr, index, pos)
                self._send_cmd('move_positioner_absolute', args)
                self.answer_type_q.put('move_positioner_absolute')
                self.answer_event.set()
            else:
                args = (self.name, pos)
                self._send_cmd('move_absolute', args)
                self.answer_type_q.put('move_absolute')
                self.answer_event.set()

        except ValueError:
            msg = ('Move failed, position must be a number.')
            wx.CallAfter(wx.MessageBox, 'Move failed', msg)

    def move_rel(self, mtr, index, pos, move_positive):
        try:
            pos = float(pos)
            if not move_positive:
                pos = -1*pos

            if self.motor_params['type'] == 'Newport_XPS':
                args = (self.name, mtr, index, pos)
                self._send_cmd('move_positioner_relative', args)
                self.answer_type_q.put('move_positioner_relative')
                self.answer_event.set()
            else:
                args = (self.name, pos)
                self._send_cmd('move_relative', args)
                self.answer_type_q.put('move_relative')
                self.answer_event.set()

        except ValueError:
            msg = ('Move failed, position must be a number.')
            wx.CallAfter(wx.MessageBox, 'Move failed', msg)

    def group_move_abs(self, group, positions):
        try:
            for i in range(len(positions)):
                positions[i] = float(positions[i])
            args = (self.name, positions)
            self._send_cmd('move_absolute', args)
            self.answer_type_q.put('move_absolute')
            self.answer_event.set()

        except ValueError:
            msg = ('Group move failed, both positions must be a number.')
            wx.CallAfter(wx.MessageBox, 'Group move failed', msg)

    def set_position(self, mtr, index, pos):
        try:
            pos = float(pos)

            if self.motor_params['type'] == 'Newport_XPS':
                args = (self.name, mtr, index, pos)
                self._send_cmd('set_positioner_position', args)
                self.answer_type_q.put('set_positioner_position')
                self.answer_event.set()
            else:
                args = (self.name, pos)
                self._send_cmd('set_position', args)
                self.answer_type_q.put('set_position')
                self.answer_event.set()

        except ValueError:
            msg = ('Set position failed, position must be a number.')
            wx.CallAfter(wx.MessageBox, 'Set position failed', msg)

    def set_low_limit(self, mtr, index, limit):
        try:
            limit = float(limit)
            args = (self.name, limit)
            kwargs = {'positioner':  mtr, 'index': index}
            self._send_cmd('set_low_limit', args, kwargs)
            self.answer_type_q.put('set_low_limit')
            self.answer_event.set()

        except ValueError:
            msg = ('Setting limit failed, limit must be a number.')
            wx.CallAfter(wx.MessageBox, 'Setting limit failed', msg)

    def set_high_limit(self, mtr, index, limit):
        try:
            limit = float(limit)
            args = (self.name, limit)
            kwargs = {'positioner':  mtr, 'index': index}
            self._send_cmd('set_high_limit', args, kwargs)
            self.answer_type_q.put('set_high_limit')
            self.answer_event.set()

        except ValueError:
            msg = ('Setting limit failed, limit must be a number.')
            wx.CallAfter(wx.MessageBox, 'Setting limit failed', msg)

    def set_v(self, mtr, index, velocity):
        try:
            velocity = float(velocity)
            args = (self.name, velocity)
            kwargs = {'positioner':  mtr, 'index': index}
            self._send_cmd('set_velocity', args, kwargs)
            self.answer_type_q.put('set_velocity')
            self.answer_event.set()

        except ValueError:
            msg = ('Setting velocity failed, velocity must be a number.')
            wx.CallAfter(wx.MessageBox, 'Setting velocity failed', msg)

    def set_a(self, mtr, index, acceleration):
        try:
            acceleration = float(acceleration)
            args = (self.name, acceleration)
            kwargs = {'positioner':  mtr, 'index': index}
            self._send_cmd('set_acceleration', args, kwargs)
            self.answer_type_q.put('set_acceleration')
            self.answer_event.set()

        except ValueError:
            msg = ('Setting acceleration failed, acceleration must be a number.')
            wx.CallAfter(wx.MessageBox, 'Setting acceleration failed', msg)

    def home(self, mtr):
        if self.motor_params['type'] == 'Newport_XPS':
            if self.motor_params['group_type'] == 'XY':
                mtr = self.motor_params['group']
            self._send_cmd('home_positioner', (self.name, mtr))
            self.answer_type_q.put('home_positioner')
            self.answer_event.set()
        else:
            self._send_cmd('home', (self.name,))
            self.answer_type_q.put('home')
            self.answer_event.set()

    def _on_np_group_type(self, evt):
        if self.np_group_type.GetStringSelection() == 'Single':
            self.newport_xps_sizer.Hide(self.np_pos2_label)
            self.newport_xps_sizer.Hide(self.np_pos2_name)
            self.control_sub_sizer.Hide(self.control_mtr2_sizer, recursive=True)
            self.control_sizer.Hide(self.group_btn_sizer, recursive=True)
        else:
            if self.newport_xps_sizer.IsShown(self.np_group_name):
                self.newport_xps_sizer.Show(self.np_pos2_label)
                self.newport_xps_sizer.Show(self.np_pos2_name)
            self.control_sub_sizer.Show(self.control_mtr2_sizer, recursive=True)
            self.control_sizer.Show(self.group_btn_sizer, recursive=True)

        self.Parent.Layout()
        self.Parent.Fit()

    def _on_type(self, evt):
        """Called when the pump type is changed in the GUI."""
        motor = self.type_ctrl.GetStringSelection()
        logger.info('Changed the motor type to %s for motor %s', motor, self.name)

        if motor == 'Newport XPS':
            self.settings_sizer.Show(self.newport_xps_sizer, recursive=True)
            self.settings_sizer.Hide(self.zaber_sizer, recursive=True)

            if self.np_group_type.GetStringSelection() == 'Single':
                self.newport_xps_sizer.Hide(self.np_pos2_label)
                self.newport_xps_sizer.Hide(self.np_pos2_name)

                self.control_sub_sizer.Hide(self.control_mtr2_sizer, recursive=True)
                self.control_sizer.Hide(self.group_btn_sizer, recursive=True)

            else:
                if self.newport_xps_sizer.IsShown(self.np_group_name):
                    self.newport_xps_sizer.Show(self.np_pos2_label)
                    self.newport_xps_sizer.Show(self.np_pos2_name)
                self.control_sub_sizer.Show(self.control_mtr2_sizer, recursive=True)
                self.control_sizer.Show(self.group_btn_sizer, recursive=True)

        elif motor == 'Zaber':
            self.settings_sizer.Hide(self.newport_xps_sizer, recursive=True)
            self.settings_sizer.Show(self.zaber_sizer, recursive=True)

        else:
            self.settings_sizer.Hide(self.newport_xps_sizer, recursive=True)
            self.settings_sizer.Hide(self.zaber_sizer, recursive=True)

        self.Parent.Layout()
        self.Parent.Fit()

    def _on_connect(self, evt):
        """Called when a pump is connected in the GUI."""
        self._connect()

    def _connect(self):
        """Initializes the pump in the PumpCommThread"""
        motor = self.type_ctrl.GetStringSelection().replace(' ', '_')
        frame = wx.FindWindowByName('MotorFrame')

        if motor == 'Newport_XPS':
            ip = self.np_ip.GetValue()
            port = self.np_port.GetValue()
            group = self.np_group_name.GetValue()
            group_type = self.np_group_type.GetStringSelection()
            mtr1 = self.np_pos1_name.GetValue()
            mtr2 = self.np_pos2_name.GetValue()

            self.mname.SetLabel(mtr1)
            self.mname2.SetLabel(mtr2)

            if group_type == 'Single':
                num_axes = 1
            else:
                num_axes = 2

            if frame.xps is None:
                frame.xps = xps_drivers.XPS()

            self.motor = NewportXPSMotor(group, frame.xps, ip, port, 20, group, num_axes)

            group_status, descrip = self.motor.get_group_status()

            self._set_status(group_status, descrip)


            v = self.motor.get_velocity(mtr1, 0)
            self.v_ctrl.SetValue(str(v))
            self.v_ctrl.SetBackgroundColour(wx.NullColour)

            a = self.motor.get_acceleration(mtr1, 0)
            self.a_ctrl.SetValue(str(a))
            self.a_ctrl.SetBackgroundColour(wx.NullColour)

            low_lim = self.motor.get_low_limit(mtr1, 0)
            self.low_limit.SetValue(str(low_lim))
            self.low_limit.SetBackgroundColour(wx.NullColour)

            high_lim = self.motor.get_high_limit(mtr1, 0)
            self.high_limit.SetValue(str(high_lim))
            self.high_limit.SetBackgroundColour(wx.NullColour)

            if group_type == 'XY':
                v2 = self.motor.get_velocity(mtr2, 1)
                self.v_ctrl2.SetValue(str(v2))
                self.v_ctrl2.SetBackgroundColour(wx.NullColour)

                a2 = self.motor.get_acceleration(mtr2, 1)
                self.a_ctrl2.SetValue(str(a2))
                self.a_ctrl2.SetBackgroundColour(wx.NullColour)

                low_lim2 = self.motor.get_low_limit(mtr2, 1)
                self.low_limit2.SetValue(str(low_lim2))
                self.low_limit2.SetBackgroundColour(wx.NullColour)

                high_lim2 = self.motor.get_high_limit(mtr2, 1)
                self.high_limit2.SetValue(str(high_lim2))
                self.high_limit2.SetBackgroundColour(wx.NullColour)

            self.motor_params = {'type': 'Newport_XPS',
                'group': group,
                'ip': ip,
                'port': port,
                'group_type': group_type,
                'mtr1': mtr1,
                'mtr2': mtr2,
                'num_axes': num_axes,
                }

        elif motor == 'Zaber':
            port = self.zaber_port.GetStringSelection()
            number = int(self.zaber_number.GetValue())
            travel = float(self.zaber_travel.GetValue())

            if port not in frame.zaber:
                binary_serial = zaber.BinarySerial(str(port))
                binary_serial.close()
                lock = threading.Lock()
                frame.zaber[port] = (binary_serial, lock)

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
                        if ZaberMotor.check_command_succeeded(reply, self.name):
                            logger.debug("Zaber device renumbered")
                        else:
                            logger.error("Zaber device renumbering failed")

                except Exception:
                    raise
                finally:
                    binary_serial.close()

                binary_serial.lock.release()

            else:
                binary_serial, lock = frame.zaber[port]

            self.motor = ZaberMotor(port, self.name, binary_serial, lock,
                number, travel)

            self.motor_params = {'type': 'Zaber',
            'port': port,
            'number': number,
            'travel': travel,
            }

        if motor != 'Newport_XPS':
            v = self.motor.get_velocity()
            self.v_ctrl.SetValue(str(v))
            self.v_ctrl.SetBackgroundColour(wx.NullColour)

            a = self.motor.get_acceleration()
            self.a_ctrl.SetValue(str(a))
            self.a_ctrl.SetBackgroundColour(wx.NullColour)

            low_lim = self.motor.get_low_limit()
            self.low_limit.SetValue(str(low_lim))
            self.low_limit.SetBackgroundColour(wx.NullColour)

            high_lim = self.motor.get_high_limit()
            self.high_limit.SetValue(str(high_lim))
            self.high_limit.SetBackgroundColour(wx.NullColour)

            self._set_status('Connected', '')

        logger.info('Connected to motor %s', self.name)
        self.connected = True
        self.connect_button.SetLabel('Reconnect')
        self._send_cmd('add_motor')
        self.answer_type_q.put('add_motor')
        self.answer_event.set()

        self.monitor_thread.start()

        return

    def _set_status(self, status, descrip):
        """
        Changes the status in the GUI.

        :param str status: The status to display.
        """
        logger.debug('Setting motor %s status to %s - %s', self.name, status, descrip)
        self.status.SetLabel(str(status))
        self.status.SetToolTip(descrip)
        self.pos_sizer.Layout()
        self.pos_sizer2.Layout()

    def _send_cmd(self, cmd, args=(), kwargs={}):
        """
        Sends commands to the pump using the ``pump_cmd_q`` that was given
        to :py:class:`PumpCommThread`.

        :param str cmd: The command to send, matching the command in the
            :py:class:`PumpCommThread` ``_commands`` dictionary.
        """
        logger.debug('Sending motor %s command %s', self.name, cmd)
        if cmd == 'move_positioner_absolute':
            self.motor_cmd_q.append(('move_positioner_absolute', args, kwargs))

        elif cmd == 'move_positioner_relative':
            self.motor_cmd_q.append(('move_positioner_relative', args, kwargs))

        elif cmd == 'move_absolute':
            self.motor_cmd_q.append(('move_absolute', args, kwargs))

        elif cmd == 'move_relative':
            self.motor_cmd_q.append(('move_relative', args, kwargs))

        elif cmd == 'set_positioner_position':
            self.motor_cmd_q.append(('set_positioner_position', args, kwargs))

        elif cmd == 'set_low_limit':
            self.motor_cmd_q.append(('set_low_limit', args, kwargs))

        elif cmd == 'set_high_limit':
            self.motor_cmd_q.append(('set_high_limit', args, kwargs))

        elif cmd == 'set_velocity':
            self.motor_cmd_q.append(('set_velocity', args, kwargs))

        elif cmd == 'set_acceleration':
            self.motor_cmd_q.append(('set_acceleration', args, kwargs))

        elif cmd == 'home_positioner':
            self.motor_cmd_q.append(('home_positioner', args, kwargs))

        elif cmd == 'home':
            self.motor_cmd_q.append(('home', args, kwargs))

        elif cmd == 'add_motor':
            self.motor_cmd_q.append(('add_motor', (self.motor, self.name), kwargs))

    def _get_response(self):
        start_time = time.time()
        while len(self.answer_q) == 0 and time.time()-start_time < 5:
            time.sleep(0.01)

        if len(self.answer_q) > 0:
            response = self.answer_q.popleft()
        else:
            response = None

        return response

    def _send_cmd_get_response(self, cmd, args=()):
        self._send_cmd(cmd, args)
        response = self._get_response()

        return response

    def _get_answer(self):
        while True:
            if self.answer_event.is_set():
                response = self._get_response()

                if response is None:
                    pass
                else:
                    cmd = self.answer_type_q.get()

                    if not response:
                        if (cmd == 'move_positioner_absolute'
                            or cmd == 'move_positioner_relative'
                            or cmd == 'move_absolute' or cmd == 'move_relative'):
                            msg = ('Move failed, check motor status.')
                            wx.CallAfter(wx.MessageBox, 'Move failed', msg)

                        elif cmd == 'set_positioner_position' or cmd == 'set_position':
                            msg = ('Set position failed, check motor status.')
                            wx.CallAfter(wx.MessageBox, 'Set position failed', msg)

                        elif (cmd == 'set_low_limit' or cmd == 'set_high_limit'):
                            msg = ('Setting limit failed, check motor status.')
                            wx.CallAfter(wx.MessageBox, 'Setting limit failed', msg)

                        elif cmd == 'set_velocity':
                            msg = ('Setting velocity failed, check motor status.')
                            wx.CallAfter(wx.MessageBox, 'Setting velocity failed', msg)

                        elif cmd == 'set_acceleration':
                            msg = ('Setting acceleration failed, check motor status.')
                            wx.CallAfter(wx.MessageBox, 'Setting acceleration failed', msg)

                        elif cmd == 'home_positioner' or cmd == 'home':
                            msg = ('Homing failed, check motor status.')
                            wx.CallAfter(wx.MessageBox, 'Homing failed', msg)

                self.answer_event.clear()
            else:
                time.sleep(0.1)

    def _update_status(self):
        interval = 0.1

        start_time = time.time()
        while True and not self.monitor_event.is_set():
            if time.time() - start_time > interval:
                if self.motor_params['type'] == 'Newport_XPS':
                    mtr1_position = self.motor.get_positioner_position(self.motor_params['mtr1'], 0)
                    wx.CallAfter(self.pos.SetLabel, str(mtr1_position))

                    if self.motor_params['num_axes'] == 2:
                        mtr2_position = self.motor.get_positioner_position(self.motor_params['mtr2'], 0)
                        wx.CallAfter(self.pos2.SetLabel, str(mtr2_position))

                    status, descrip = self.motor.get_group_status()
                    wx.CallAfter(self._set_status, status, descrip)

                    if int(status)>=43 and int(status)<=45:
                        wx.CallAfter(self.moving.SetLabel, 'True')
                    else:
                        wx.CallAfter(self.moving.SetLabel, 'False')

                else:
                    mtr_position = self.motor.position
                    wx.CallAfter(self.pos.SetLabel, str(mtr_position))

                    if self.motor.is_moving():
                        wx.CallAfter(self.moving.SetLabel, 'True')
                    else:
                        wx.CallAfter(self.moving.SetLabel, 'False')

                start_time = time.time()
            else:
                time.sleep(.01)


    def on_exit(self):
        logger.info('Exiting MotorPanel %s', self.name)
        self.monitor_event.set()
        if self.monitor_thread.is_alive():
            self.monitor_thread.join()

        try:
            self.motor.stop()
            self.motor.disconnect()
        except Exception:
            pass


class MotorFrame(wx.Frame):
    """
    A lightweight frame allowing one to work with arbitrary number of pumps.
    Only meant to be used when the pumpcon module is run directly,
    rather than when it is imported into another program.
    """
    def __init__(self, *args, **kwargs):
        """
        Initializes the pump frame. Takes args and kwargs for the wx.Frame class.
        """
        super(MotorFrame, self).__init__(*args, **kwargs)
        logger.debug('Setting up the PumpFrame')
        self.motor_cmd_q = deque()
        self.motor_answer_q = deque()
        self.abort_event = threading.Event()
        self.motor_con = MotorCommThread(self.motor_cmd_q, self.motor_answer_q, self.abort_event, name='MotorCon')
        self.motor_con.start()

        self.Bind(wx.EVT_CLOSE, self._on_exit)

        self.motors =[]

        self.xps = None
        self.zaber = {}

        self._get_ports()

        top_sizer = self._create_layout()

        self.SetSizer(top_sizer)

        self.Fit()
        self.Raise()

        self._initmotors()

    def _create_layout(self):
        """Creates the layout"""
        motor_panel = MotorPanel(self, wx.ID_ANY, 'stand_in', self.motor_cmd_q,
            self.motor_answer_q, self.motor_con.known_motors, 'stand_in', self.ports)

        self.motor_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.motor_sizer.Add(motor_panel, flag=wx.RESERVE_SPACE_EVEN_IF_HIDDEN)

        self.motor_sizer.Hide(motor_panel, recursive=True)

        button_panel = wx.Panel(self)

        add_motor = wx.Button(button_panel, label='Add motor')
        add_motor.Bind(wx.EVT_BUTTON, self._on_addmotor)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(add_motor)

        button_panel_sizer = wx.BoxSizer(wx.VERTICAL)
        button_panel_sizer.Add(wx.StaticLine(button_panel), flag=wx.EXPAND|wx.TOP|wx.BOTTOM, border=2)
        button_panel_sizer.Add(button_sizer, flag=wx.ALIGN_RIGHT|wx.BOTTOM|wx.RIGHT, border=2)

        button_panel.SetSizer(button_panel_sizer)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(self.motor_sizer, flag=wx.EXPAND)
        top_sizer.Add(button_panel, flag=wx.EXPAND|wx.ALL, border=5)

        return top_sizer

    def _initmotors(self):
        """
        This is a convenience function for initalizing pumps on startup, if you
        already know what pumps you want to add. You can comment it out in
        the ``__init__`` if you want to not load any pumps on startup.

        If you want to add pumps here, add them to the ``setup_pumps`` list.
        Each entry should be an iterable with the following parameters: name,
        pump type, comport, arg list, and kwarg dict in that order. How the
        arg list and kwarg dict are handled are defined in the
        :py:func:`PumpPanel._initpump` function, and depends on the pump type.
        """
        if not self.motors:
            self.motor_sizer.Remove(0)

        # setup_motors = [(['GROUP1', 'Newport XPS', '164.54.204.65', '5001',
        #     'Single', 'GROUP1.POSITIONER', ''], {}),
        #             ]

        setup_motors = [(['XY', 'Newport XPS', '164.54.204.74', '5001',
            'XY', 'XY.X', 'XY.Y'], {}),
                    ]

        logger.info('Initializing %s motors on startup', str(len(setup_motors)))

        for motor in setup_motors:
            new_motor = MotorPanel(self, wx.ID_ANY, motor[0][0], self.motor_cmd_q,
                self.motor_answer_q, self.motor_con.known_motors, motor[0][0], self.ports,
                motor[0][1], motor[0], motor[1])

            self.motor_sizer.Add(new_motor, border=5, flag=wx.LEFT|wx.RIGHT)
            self.motors.append(new_motor)

        self.Layout()
        self.Fit()

    def _on_addmotor(self, evt):
        """
        Called when the Add pump button is used. Adds a new pump to the control
        panel.

        .. note:: Pump names must be distinct.
        """
        if not self.motors:
            self.motor_sizer.Remove(0)

        dlg = wx.TextEntryDialog(self, "Enter motor name:", "Create new motor")
        if dlg.ShowModal() == wx.ID_OK:
            name = dlg.GetValue()
            for motor in self.motors:
                if name == motor.name:
                    msg = "Motor names must be distinct. Please choose a different name."
                    wx.MessageBox(msg, "Failed to add motor")
                    logger.debug('Attempted to add a motor with the same name (%s) as another motor.', name)
                    return

            new_motor = MotorPanel(self, wx.ID_ANY, name, self.motor_cmd_q,
                self.motor_answer_q, self.motor_con.known_motors, name, self.ports)
            logger.info('Added new motor %s to the motor control panel.', name)
            self.motor_sizer.Add(new_motor, border=5, flag=wx.LEFT|wx.RIGHT)
            self.motors.append(new_motor)

            self.Layout()
            self.Fit()

        return

    def _get_ports(self):
        """
        Gets a list of active comports.

        .. note:: This doesn't update after the program is opened, so you need
            to start the program after all pumps are connected to the computer.
        """
        port_info = list_ports.comports()
        self.ports = [port.device for port in port_info]

        logger.debug('Found the following comports for the ValveFrame: %s', ' '.join(self.ports))

    def _on_exit(self, evt):
        """Stops all current pump motions and then closes the frame."""
        logger.debug('Closing the MotorFrame')
        for motor_panel in self.motors:
            motor_panel.on_exit()
        self.motor_con.stop()
        self.motor_con.join()
        while self.motor_con.is_alive():
            time.sleep(0.001)
        self.Destroy()

if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.INFO)
    # h1.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    # my_motor = NewportXPSMotor('X', '164.54.204.65', 5001, 20, 'GROUP1', 1)
    # my_motor = NewportXPSMotor('XY', '164.54.204.65', 5001, 20, 'XY', 2)

    # group_status, descrip = my_motor.get_group_status()
    # if group_status == 42:
    #     my_motor.home()

    # 1 axis
    # my_motor.move_absolute([20.0])
    # current_position = my_motor.position
    # print(current_position)
    # my_motor.move_relative([-20.0])
    # current_position = my_motor.position
    # print(current_position)

    # my_motor.position = [5.0]
    # my_motor.move_relative([-10.0])
    # current_position = my_motor.position
    # print(current_position)
    # my_motor.move_absolute([5.0])
    # current_position = my_motor.position
    # print(current_position)

    # my_motor.get_positioner_position('GROUP1.POSITIONER', 0)
    # my_motor.set_positioner_position('GROUP1.POSITIONER', 0, 0)
    # current_position = my_motor.position
    # print(current_position)

    # my_motor.move_positioner_absolute('GROUP1.POSITIONER', 0, -10)
    # current_position = my_motor.position
    # print(current_position)
    # my_motor.move_positioner_relative('GROUP1.POSITIONER', 0, 10)
    # current_position = my_motor.position
    # print(current_position)

    # my_motor.get_high_limit('GROUP1.POSITIONER', 0)
    # my_motor.get_low_limit('GROUP1.POSITIONER', 0)

    # my_motor.set_high_limit(10, 'GROUP1.POSITIONER', 0)
    # my_motor.set_low_limit(-10, 'GROUP1.POSITIONER', 0)

    # velocity = my_motor.get_velocity('GROUP1.POSITIONER', 0)
    # print(velocity)

    # my_motor.move_positioner_absolute('GROUP1.POSITIONER', 0, -10)
    # current_position = my_motor.position
    # print(current_position)

    # my_motor.set_velocity(25, 'GROUP1.POSITIONER', 0)

    # my_motor.move_positioner_relative('GROUP1.POSITIONER', 0, 10)
    # current_position = my_motor.position
    # print(current_position)

    # my_motor.set_velocity(10, 'GROUP1.POSITIONER', 0)


    # 2 axis xy group
    # my_motor.move_absolute([20.0, 20.0])
    # current_position = my_motor.position
    # print(current_position)
    # my_motor.move_relative([-20.0, -20.0])
    # current_position = my_motor.position
    # print(current_position)

    # my_motor.position = [5.0, 5.0]
    # my_motor.move_relative([-10.0, -10.0])
    # current_position = my_motor.position
    # print(current_position)
    # my_motor.move_absolute([5.0, 5.0])
    # current_position = my_motor.position
    # print(current_position)

    # my_motor.get_positioner_position('XY.Y', 1)
    # my_motor.set_positioner_position('XY.Y', 1, 0)
    # current_position = my_motor.position
    # print(current_position)

    # my_motor.move_positioner_absolute('XY.Y', 1, -10)
    # current_position = my_motor.position
    # print(current_position)
    # my_motor.move_positioner_relative('XY.Y', 1, 10)
    # current_position = my_motor.position
    # print(current_position)

    # my_motor.get_high_limit('XY.Y', 1)
    # my_motor.get_low_limit('XY.Y', 1)

    # my_motor.set_high_limit(20, 'XY.Y', 1)
    # my_motor.set_low_limit(-20, 'XY.Y', 1)

    # velocity = my_motor.get_velocity('XY.X', 0)
    # print(velocity)

    # my_motor.move_positioner_absolute('XY.X', 0, -10)
    # current_position = my_motor.position
    # print(current_position)

    # my_motor.set_velocity(25, 'XY.X', 0)

    # my_motor.move_positioner_relative('XY.X', 0, 10)
    # current_position = my_motor.position
    # print(current_position)

    # my_motor.set_velocity(0.1, 'XY.X', 0)
    # my_motor.set_velocity(0.1, 'XY.Y', 1)
    # my_motor.stop_position_compare('XY.X')
    # my_motor.set_position_compare('XY.X', 0, 0., 10., 0.1)
    # my_motor.set_position_compare_pulse('XY.X', 10, 12)
    # min_pos, max_pos, step, enable = my_motor.get_position_compare('XY.X', 0)
    # my_motor.start_position_compare('XY.X')
    # min_pos, max_pos, step, enable = my_motor.get_position_compare('XY.X', 0)
    # print(enable)
    # my_motor.move_positioner_absolute('XY.X', 0, 1)
    # my_motor.stop_position_compare('XY.X')
    # min_pos, max_pos, step, enable = my_motor.get_position_compare('XY.X', 0)
    # print(enable)
    # my_motor.set_velocity('XY.X', 0, 50)
    # my_motor.move_positioner_absolute('XY.X', 0, 0)

    # my_motor.set_position_compare('XY.Y', 1, 0., 1., .1)
    # my_motor.set_position_compare_pulse('XY.Y', 0.2, 12)
    # # min_pos, max_pos, step, enable = my_motor.get_position_compare('XY.Y', 0)
    # my_motor.start_position_compare('XY.Y')
    # # min_pos, max_pos, step, enable = my_motor.get_position_compare('XY.Y', 0)
    # # print(enable)
    # my_motor.move_positioner_absolute('XY.Y', 1, 1)
    # my_motor.stop_position_compare('XY.Y')
    # # min_pos, max_pos, step, enable = my_motor.get_position_compare('XY.Y', 0)
    # # print(enable)
    # my_motor.set_velocity('XY.Y', 1, 50)
    # my_motor.move_positioner_absolute('XY.Y', 1, 0)

    # my_motor.get_position_compare_pulse('XY.X')
    # my_motor.set_position_compare_pulse('XY.Y', 10, 1)
    # my_motor.get_position_compare_pulse('XY.X')
    # my_motor.set_position_compare_pulse('XY.X', 0.2, 0.075)

    # Trial with real-ish parameters
    # dist = 20.0
    # velocity = 5.0
    # angle = 45.0 *(np.pi/180.0)

    # dx = dist*np.cos(angle)
    # dy = dist*np.sin(angle)

    # vx = velocity*np.cos(angle)
    # # vy = velocity*np.sin(angle)
    # vy=20

    # my_motor.set_velocity(vx, 'XY.X', 0)
    # my_motor.set_velocity(vy, 'XY.Y', 1)

    # my_motor.move_absolute([dx, dy])

    # my_motor.move_relative([-dx, -dy])

    # my_motor.disconnect()

    # pmp_cmd_q = deque()
    # return_q = queue.Queue()
    # abort_event = threading.Event()
    # my_pumpcon = PumpCommThread(pmp_cmd_q, return_q, abort_event, 'PumpCon')
    # my_pumpcon.start()

    # init_cmd = ('connect', ('COM6', 'pump2', 'VICI_M50'),
    #     {'flow_cal': 626.2, 'backlash_cal': 9.278})
    # fr_cmd = ('set_flow_rate', ('pump2', 2000), {})
    # start_cmd = ('start_flow', ('pump2',), {})
    # stop_cmd = ('stop', ('pump2',), {})
    # dispense_cmd = ('dispense', ('pump2', 200), {})
    # aspirate_cmd = ('aspirate', ('pump2', 200), {})
    # moving_cmd = ('is_moving', ('pump2', return_q), {})

    # pmp_cmd_q.append(init_cmd)
    # pmp_cmd_q.append(fr_cmd)
    # pmp_cmd_q.append(start_cmd)
    # pmp_cmd_q.append(dispense_cmd)
    # pmp_cmd_q.append(aspirate_cmd)
    # pmp_cmd_q.append(moving_cmd)
    # time.sleep(5)
    # pmp_cmd_q.append(stop_cmd)
    # my_pumpcon.stop()


    # #Testing Zaber

    # binary_serial = zaber.BinarySerial(str("/dev/tty.usbserial-A6023E9E"))
    # binary_serial.close()

    # binary_serial.lock.acquire()

    # try:
    #     binary_serial.open()
    #     while binary_serial.can_read():
    #         reply = binary_serial.read()
    #     # Device number 0, command number 2, renumber.
    #     command = zaber.BinaryCommand(0, 2)
    #     binary_serial.write(command)

    #     time.sleep(5)

    #     nmotors = 3

    #     for i in range(nmotors):
    #         reply = binary_serial.read()
    #         if ZaberMotor.check_command_succeeded(reply):
    #             print("Device renumbered")
    #         else:
    #             print("Device renumbering failed")

    # except Exception:
    #     raise
    # finally:
    #     binary_serial.close()

    # binary_serial.lock.release()

    # lock = threading.Lock()

    # motor_x = ZaberMotor("/dev/tty.usbserial-A6023E9E", 'x', binary_serial,
    # lock, 1, 150)
    # motor_y = ZaberMotor("/dev/tty.usbserial-A6023E9E", 'y', binary_serial,
    # lock, 2, 150)
    # motor_z = ZaberMotor("/dev/tty.usbserial-A6023E9E", 'z', binary_serial,
    # lock, 3, 75)





    app = wx.App()
    logger.debug('Setting up wx app')
    frame = MotorFrame(None, title='Motor Control', name='MotorFrame')
    frame.Show()
    app.MainLoop()


