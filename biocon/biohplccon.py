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
from collections import OrderedDict, deque
import logging
import sys
import copy
import os

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import wx
import serial
import serial.tools.list_ports as list_ports
from six import string_types

import utils
import valvecon

agilent_path = os.path.abspath(os.path.join('.', __file__, '..', '..', '..',
    'agilent-control'))
if agilent_path not in os.sys.path:
    os.sys.path.append(agilent_path)

import agilentcon.hplccon as hplccon


class AgilentHPLC2Pumps(hplccon.AgilentHPLC):
    """
    Specific control for the SEC-SAXS Agilent HPLC with dual pumps
    """

    def __init__(self, name, device, hplc_args={}, selector_valve_args={},
        outlet_valve_args={}, purge1_valve_args={}, purge2_valve_args={},
        pump1_id='', pump2_id=''):
        """
        Initializes the HPLC plus valves

        Parameters
        ----------
        name: str
            Device name
        device: str
            Ignored. Dummy argument so the format is consistent with other devices.
        hplc_args: dict
            Dictionary of input arguments for the Agilent HPLC
        selector_valve_args: dict
            Dictionary of input arguments for the selector valve
        outlet_valve_args: dict
            Dictionary of input arguments for the outlet valve
        purge1_valve_args: dict
            Dictionary of the input arguments for the flowpath 1 purge valve
        purge2_valve_args: dict
            Dictionary of the input arguments for the flowpath 2 purge valve
        """

        self._active_flow_path = None

        # Defines valve positions for various states
        self._flow_path_positions = {
            1   : {'selector': 1, 'outlet': 1},
            2   : {'selector': 2, 'outlet': 2},
            }

        self._purge_positions = {
            1   : {'purge1': 2},
            2   : {'purge2': 2},
            }

        self._column_positions = {
            1   : {'purge1': 1},
            2   : {'purge2': 1},
            }

        # Connect valves
        self._connect_valves(selector_valve_args, outlet_valve_args,
            purge1_valve_args, purge2_valve_args)

        # Connect HPLC
        hplc_device_type = hplc_args['args'][0]
        hplc_device = hplc_args['args'][1]
        hplc_kwargs = hplc_args['kwargs']

        hplccon.AgilentHPLC.__init__(self, name, hplc_device, **hplc_kwargs)

        # Other definitions
        self._pump1_id = pump1_id
        self._pump2_id = pump2_id

        self._default_purge_rate = 5.0 #mL/min
        self._default_purge_accel = 10.0 #mL/min
        self._pre_purge_flow1 = None
        self._pre_purge_flow2 = None
        self._pre_purge_flow_accel1 = None
        self._pre_purge_flow_accel2 = None
        self._purging_flow1 = False
        self._purging_flow2 = False
        self._remaining_purge1_vol = None
        self._remaining_purge2_vol = None
        self._target_purge_flow1 = 0.0
        self._target_purge_flow2 = 0.0
        self._target_purge_accel1 = 0.0
        self._target_purge_accel2 = 0.0
        self._stop_before_purging1 = True
        self._stop_before_purging2 = True

        self._monitor_purge_evt = threading.Event()
        self._terminate_monitor_purge = threading.Event()
        self._monitor_purge_thread = threading.Thread(
            target=self._monitor_purge)
        self._monitor_purge_thread.daemon = True
        self._monitor_purge_thread.start()

        self._switching_flow_path = False

        self._monitor_switch_evt = threading.Event()
        self._terminate_monitor_switch = threading.Event()
        self._abort_switch = threading.Event()
        self._monitor_switch_thread = threading.Thread(
            target=self._monitor_switch)
        self._monitor_switch_thread.daemon = True
        self._monitor_switch_thread.start()



    def _connect_valves(self, sv_args, ov_args, p1_args, p2_args):
        sv_name = sv_args['name']
        sv_arg_list = sv_args['args']
        sv_kwarg_list = sv_args['kwargs']
        sv_device_type = sv_arg_list[0]
        sv_comm = sv_arg_list[1]

        self._selector_valve = valvecon.known_valves[sv_device_type](sv_name,
            sv_comm, **sv_kwarg_list)
        self._selector_valve.connect()

        ov_name = ov_args['name']
        ov_arg_list = ov_args['args']
        ov_kwarg_list = ov_args['kwargs']
        ov_device_type = ov_arg_list[0]
        ov_comm = ov_arg_list[1]

        self._outlet_valve = valvecon.known_valves[ov_device_type](ov_name,
            ov_comm, **ov_kwarg_list)
        self._outlet_valve.connect()

        p1_name = p1_args['name']
        p1_arg_list = p1_args['args']
        p1_kwarg_list = p1_args['kwargs']
        p1_device_type = p1_arg_list[0]
        p1_comm = p1_arg_list[1]

        self._purge1_valve = valvecon.known_valves[p1_device_type](p1_name,
            p1_comm, **p1_kwarg_list)
        self._purge1_valve.connect()

        p2_name = p2_args['name']
        p2_arg_list = p2_args['args']
        p2_kwarg_list = p2_args['kwargs']
        p2_device_type = p2_arg_list[0]
        p2_comm = p2_arg_list[1]

        self._purge2_valve = valvecon.known_valves[p2_device_type](p2_name,
            p2_comm, **p2_kwarg_list)
        self._purge2_valve.connect()

        self._valves = {
            'selector'  : self._selector_valve,
            'outlet'    : self._outlet_valve,
            'purge1'    : self._purge1_valve,
            'purge2'    : self._purge2_valve,
            }

        for flow_path in self._flow_path_positions:
            active_flow_path = True

            for valve, fp_pos in self._flow_path_positions[flow_path].items():
                current_pos = self.get_valve_position(valve)

                if int(fp_pos) != int(current_pos):
                    active_flow_path = False
                    break

            if active_flow_path:
                self._active_flow_path = flow_path
                break

    def get_valve_position(self, valve_id):
        """
        Gets the position of the specified valve.

        Parameters
        ----------
        valve_id: str
            Valve name. Can be selector, outlet, purge1, purge2

        Returns
        -------
        position: str
            The valve position
        """
        valve = self._valves[valve_id]
        position = valve.get_position()
        return position

    def get_active_flow_path(self):
        """
        Gets the current active flow path (which path is connected to the
        multisampler and active oulet port). Note that being the active
        flow path does not guarantee that purge is off for that flow path.

        Returns
        -------
        active_flow_path: int
            The active flow path, either 1 or 2.
        """
        return copy.copy(self._active_flow_path)

    def get_purge_status(self, flow_path):
        """
        Gets the purge status of the specified flow path.

        Parameters
        ----------
        flow_path: int
            The flow path to get the status for. Either 1 or 2.

        Returns
        -------
        is_purging: bool
            True if the flow path is purging, False if not.
        remaining_volume: float
            The remaining volume to purge.
        purge_rate: float
            The target rate for the purge (note that depending on the
            purge acceleration not all of the purge may run at this rate)
        """
        flow_path = int(flow_path)

        if flow_path == 1:
            is_purging = copy.copy(self._purging_flow1)
            remaining_volume = copy.copy(self._remaining_purge1_vol)
            purge_rate = copy.copy(self._target_purge_flow1)

        elif flow_path == 2:
            is_purging = copy.copy(self._purging_flow2)
            remaining_volume = copy.copy(self._remaining_purge2_vol)
            purge_rate = copy.copy(self._target_purge_flow2)

        return is_purging, remaining_volume, purge_rate

    def get_hplc_flow_rate(self, flow_path):
        """
        Gets the flow rate of the specified flow path
        Parameters
        ----------
        flow_path: int
            The flow path to get the status for. Either 1 or 2.

        Returns
        -------
        flow_rate: float
            The flow rate of the specified flow path.
        """
        flow_path = int(flow_path)

        if flow_path == 1:
            flow_rate = self.get_flow_rate(self._pump1_id)
        elif flow_path == 2:
            flow_rate = self.get_flow_rate(self._pump2_id)

        return flow_rate

    def get_flow_path_switch_status(self):
        """
        Gets whether or not the HPLC is currently switching flow paths.

        Returns
        -------
        is_switching: bool
            True if switching, otherwise False.
        """
        return copy.copy(self._switching_flow_path)

    def purge_flow_path(self, flow_path, purge_volume, purge_rate=None,
        purge_accel=None, restore_flow_after_purge=True,
        purge_with_sample=False, stop_before_purge=True,
        stop_after_purge=True):
        """
        Purges the specified flow path. Note that attempting to start a purge
        on a flow path that is already purging will result in an error

        Parameters
        ----------
        flow_path: int
            The flow path to purge. Either 1 or 2.
        purge_volume: float
            Volume in mL to be purged.
        purge_rate: float
            Flow rate to use for purging. If no rate supplied, the device's
            default purge rate is used.
        purge_accel: float
            Flow acceleration to use for purging. If no rate is supplied, the
            device's default purge rate is used.
        restore_flow_after_purge: bool
            Whether the flow rate should be restored to the current flow rate
            after purging is done. If False, flow rate after purging will be 0.
        purge_with_sample: bool
            Checks whether there are samples in the run queue. If there are, and
            the run queue is not paused and you are purging the active flow
            path, you must pass True for this value to carry out the purge.
            Otherwise the purge will not run.
        stop_before_purge: bool
            Stops the flow before switching the purge valve to purge position.
        stop_after_purge: bool
            Stops the flow after purging before switching the purge valve
            back to standard position. If not True, then the flow is ramped
            to the final value before the valve is switched. If True, the
            flow is ramped to zero, the valve switched, and the flow ramped
            back to the final value.
        """
        flow_path = int(flow_path)

        if ((flow_path == 1 and self._purging_flow1) or
            (flow_path == 2 and self._purging_flow2)):
            logger.error('HPLC %s flow path %s is already purging, so a new '
                'purge cannot be started', self.name, flow_path)

        else:
            do_purge = self._check_purge_sample_status(flow_path,
                purge_with_sample)

            if do_purge:
                self._start_purge(flow_path, purge_volume, purge_rate,
                    purge_accel, restore_flow_after_purge, stop_before_purge,
                    stop_after_purge)

    def _start_purge(self, flow_path, purge_volume, purge_rate, purge_accel,
            restore_flow_after_purge, stop_before_purge, stop_after_purge):
        if purge_rate is None:
            purge_rate = self._default_purge_rate
        if purge_accel is None:
            purge_accel = self._default_purge_accel

        if flow_path == 1:
            if restore_flow_after_purge:
                self._pre_purge_flow1 = self.get_flow_rate(self._pump1_id)
            else:
                self._pre_purge_flow1 = None

            self._pre_purge_flow_accel1 = self.get_flow_accel(self._pump1_id)
            self._remaining_purge1_vol = purge_volume
            self._target_purge_flow1 = purge_rate
            self._target_purge_accel1 = purge_accel
            self._stop_before_purging1 = stop_before_purge
            self._stop_after_purging1 = stop_after_purge
            self._purging_flow1 = True

        elif flow_path == 2:
            if restore_flow_after_purge:
                self._pre_purge_flow2 = self.get_flow_rate(self._pump2_id)
            else:
                self._pre_purge_flow2 = None

            self._pre_purge_flow_accel2 = self.get_flow_accel(self._pump2_id)
            self._remaining_purge2_vol = purge_volume
            self._target_purge_flow2 = purge_rate
            self._target_purge_accel2 = purge_accel
            self._stop_before_purging2 = stop_before_purge
            self._stop_after_purging2 = stop_after_purge
            self._purging_flow2 = True

        self._monitor_purge_evt.set()

        logger.info(('HPLC %s started purge of flow path %s for %s mL '
                '%s mL/min'), self.name, flow_path, purge_volume, purge_rate)


    def _check_purge_sample_status(self, flow_path, purge_with_sample):
        do_purge = True

        if self._active_flow_path == flow_path:
            samples_being_run = self._check_samples_being_run()

            if samples_being_run and not purge_with_sample:
                logger.error(('HPLC %s cannot purge flow path %s because '
                    'samples are being run.'), self.name, flow_path)
                do_purge = False

        return do_purge

    def _check_samples_being_run(self):
        run_queue_status = self.get_run_queue_status()
        run_queue = self.get_run_queue()
        acquiring = False
        pending = False

        for item in run_queue:
            if item[1] == 'Acquiring':
                acquiring = True
            if (item[1] == 'Pending' or item[1] == 'Validating'
                or item[1] == 'Submitted' or item[1] == 'Editing'
                or item[1] == 'Scanning' or item[1] == 'InReview'
                or item[1] == 'Suspended'):
                pending = True

        if acquiring or (pending and run_queue_status != 'Paused'):
            samples_being_run = True
        else:
            samples_being_run = False

        return samples_being_run

    def _monitor_purge(self):
        monitoring_flow1 = False
        monitoring_flow2 = False
        stopping_flow1 = False
        stopping_flow2 = False
        stopping_initial_flow1 = False
        stopping_initial_flow2 = False

        while not self._terminate_monitor_purge.is_set():
            self._monitor_purge_evt.wait()

            if (self._purging_flow1 and not monitoring_flow1
                and not stopping_flow1 and not stopping_initial_flow1):
                stopping_initial_flow1 = True
                monitoring_flow1 = False
                stopping_flow1 = False

                if self._stop_before_purging1:
                    self.set_flow_rate(0, self._pump1_id)

            if (self._purging_flow2 and not monitoring_flow2
                and not stopping_flow2 and not stopping_initial_flow2):
                stopping_initial_flow2 = True
                monitoring_flow2 = False
                stopping_flow2 = False

                if self._stop_before_purging2:
                    self.set_flow_rate(0, self._pump2_id)

            if stopping_initial_flow1:
                if self._stop_before_purging1:
                    current_flow1 = self.get_flow_rate(self._pump1_id)

                    if current_flow1 == 0:
                        ready_to_purge = True
                    else:
                        ready_to_purge = False
                else:
                    ready_to_purge = True

                if ready_to_purge:
                    for name, pos in self._purge_positions[1].items():
                        current_pos = int(self.get_valve_position(name))

                        if current_pos != pos:
                            self.set_valve_position(name, pos)

                    self.set_flow_accel(self._target_purge_accel1,
                        self._pump1_id)

                    flow_accel1 = self.get_flow_accel(self._pump1_id)
                    previous_flow1 = self.get_flow_rate(self._pump1_id)
                    previous_time1 = time.time()
                    update_time1 = previous_time1

                    self.set_flow_rate(self._target_purge_flow1, self._pump1_id)

                    stopping_initial_flow1 = False
                    monitoring_flow1 = True

            if stopping_initial_flow2:
                if self._stop_before_purging2:
                    current_flow2 = self.get_flow_rate(self._pump2_id)

                    if current_flow2 == 0:
                        ready_to_purge = True
                    else:
                        ready_to_purge = False
                else:
                    ready_to_purge = True

                if ready_to_purge:
                    for name, pos in self._purge_positions[2].items():
                        current_pos = int(self.get_valve_position(name))

                        if current_pos != pos:
                            self.set_valve_position(name, pos)

                    self.set_flow_accel(self._target_purge_accel2,
                        self._pump2_id)

                    flow_accel2 = self.get_flow_accel(self._pump2_id)
                    previous_flow2 = self.get_flow_rate(self._pump2_id)
                    previous_time2 = time.time()
                    update_time2 = previous_time2

                    self.set_flow_rate(self._target_purge_flow2, self._pump2_id)

                    stopping_initial_flow2 = False
                    monitoring_flow2 = True

            if monitoring_flow1:
                current_flow1 = self.get_flow_rate(self._pump1_id)
                current_time1 = time.time()
                delta_vol1 = (((current_flow1 + previous_flow1)/2./60.)
                    *(current_time1-previous_time1))

                self._remaining_purge1_vol -= delta_vol1

                if flow_accel1 > 0:
                    stop_vol1 = (current_flow1/flow_accel1)*(current_flow1/2.)
                else:
                    stop_vol1 = 0

                previous_time1 = current_time1
                previous_flow1 = current_flow1

                if self._remaining_purge1_vol - stop_vol1 <= 0:
                    monitoring_flow1 = False
                    stopping_flow1 = True

                    if self._pre_purge_flow1 is None:
                        final_flow1 = 0
                    else:
                        final_flow1 = self._pre_purge_flow1

                    if self._stop_after_purging1:
                        self.set_flow_rate(0, self._pump1_id)
                    else:
                        self.set_flow_rate(final_flow1, self._pump1_id)


                if current_time1 - update_time1 > 15:
                    update_time1 = current_time1

            if monitoring_flow2:
                current_flow2 = self.get_flow_rate(self._pump2_id)
                current_time2 = time.time()
                delta_vol2 = (((current_flow2 + previous_flow2)/2./60.)
                    *(current_time2-previous_time2))

                self._remaining_purge2_vol -= delta_vol2

                if flow_accel2 > 0:
                    stop_vol2 = (current_flow2/flow_accel2)*(current_flow2/2.)
                else:
                    stop_vol2 = 0

                previous_time2 = current_time2
                previous_flow2 = current_flow2

                if self._remaining_purge2_vol - stop_vol2 <= 0:
                    monitoring_flow2 = False
                    stopping_flow2 = True

                    if self._pre_purge_flow2 is None:
                        final_flow2 = 0
                    else:
                        final_flow2 = self._pre_purge_flow2

                    if self._stop_after_purging2:
                        self.set_flow_rate(0, self._pump2_id)
                    else:
                        self.set_flow_rate(final_flow2, self._pump2_id)

                if current_time2 - update_time2 > 15:
                    update_time2 = current_time2


            if stopping_flow1:
                current_flow1 = self.get_flow_rate(self._pump1_id)
                current_time1 = time.time()

                if ((self._stop_after_purging1 and current_flow1 == 0)
                    or (not self._stop_after_purging1
                    and current_flow1 == final_flow1)):
                    self.set_flow_accel(self._pre_purge_flow_accel1,
                        self._pump1_id)

                    stopping_flow1 = False
                    self._purging_flow1 = False

                    for name, pos in self._column_positions[1].items():
                        current_pos = int(self.get_valve_position(name))

                        if current_pos != pos:
                            self.set_valve_position(name, pos)

                    if self._stop_after_purging1:
                        self.set_flow_rate(final_flow1, self._pump1_id)

                    logger.info(('HPLC %s finished purging flow path 1. '
                        'Flow rate set to %s'), self.name, final_flow1)

                if current_time1 - update_time1 > 15:
                    update_time1 = current_time1

            if stopping_flow2:
                current_flow2 = self.get_flow_rate(self._pump2_id)
                current_time2 = time.time()

                if ((self._stop_after_purging2 and current_flow2 == 0)
                    or (not self._stop_after_purging2
                    and current_flow2 == final_flow2)):
                    self.set_flow_accel(self._pre_purge_flow_accel2,
                        self._pump2_id)

                    stopping_flow2 = False
                    self._purging_flow2 = False

                    for name, pos in self._column_positions[2].items():
                        current_pos = int(self.get_valve_position(name))

                        if current_pos != pos:
                            self.set_valve_position(name, pos)

                    if self._stop_after_purging2:
                        self.set_flow_rate(final_flow2, self._pump2_id)

                    logger.info(('HPLC %s finished purging flow path 2. '
                        'Flow rate set to %s'), self.name, final_flow2)

                if current_time2 - update_time2 > 15:
                    update_time2 = current_time2


            if not self._purging_flow1 and not self._purging_flow2:
                self._monitor_purge_evt.clear()
            else:
                time.sleep(0.1)

    def set_valve_position(self, valve_id, position):
        """
        Sets the position of the specified valve.

        Parameters
        ----------
        valve_id: str
            Valve name. Can be selector, outlet, purge1, purge2
        position: int
            Position to be set

        Returns
        -------
        success: bool
            Whether or not the position was successfully set.
        """
        valve = self._valves[valve_id]
        success = valve.set_position(position)
        return success

    def set_active_flow_path(self, flow_path, stop_flow1=False,
        stop_flow2=False, restore_flow_after_switch=True, purge_active=True,
        purge_volume=1.0, purge_rate=None, purge_accel=None,
        switch_with_sample=False):
        """
        Sets the active flow path (i.e. which one is connected to the
        multisampler and the active port on the outlet).

        Parameters
        ----------
        flow_path: int
            The active flow path to set. Either 1 or 2.
        stop_flow1: bool
            Whether flow from pump 1 should be stopped while the
            flow path is switched.
        stop_flow2: bool
            Whether flow from pump 2 should be stopped while the
            flow path is switched.
        restore_flow_after_switch: bool
            Whether the flow rate should be restored to the current flow rate
            after switching is done. Note that this is only needed if either
            stop_flow is True. If False, any flow that is stopped will not
            be resumed after switching.
        purge_active: bool
            If true, this will do a purge of the active flow path after
            switching. Commonly used to purge the multisampler flow path on
            switching. Note that if the active flow path (after switching)
            is currently purging then no additional purge will be done.
        purge_volume: float
            Volume in mL to be purged if purge_active is True.
        purge_rate: float
            Flow rate to use for purging. If no rate supplied, the device's
            default purge rate is used.
        purge_accel: float
            Flow acceleration to use for purging. If no rate is supplied, the
            device's default purge rate is used.
        switch_with_sample: bool
            Checks whether there are samples in the run queue. If there are,
            and the run queue is not paused you must pass True for this
            value to switch the active flow path. Otherwise the flow path
            will not switch.
        """
        flow_path = int(flow_path)

        if self._active_flow_path == flow_path:
            logger.info('HPLC %s already set to active flow path %s',
                self.name, flow_path)
        elif self._switching_flow_path:
            logger.error('HPLC %s cannot switch flow paths because a switch '
                'is already underway.', self.name)
        else:
            samples_being_run = self._check_samples_being_run()

            if samples_being_run and not switch_with_sample:
                logger.error(('HPLC %s cannot switch active flow path because '
                    'samples are being run'), self.name)

            else:
                if ((self._purging_flow1 and flow_path == 1) or
                    (self._purging_flow2 and flow_path == 2)):
                    if purge_active:
                        logger.info(('HPLC %s flow path %s is already purging '
                            'no additional purge will be done'), self.name,
                            flow_path)

                        purge_active = False

                self._switch_args = {
                    'flow_path': flow_path,
                    'stop_flow1': stop_flow1,
                    'stop_flow2': stop_flow2,
                    'restore_flow_after_switch': restore_flow_after_switch,
                    'purge_active': purge_active,
                    'purge_volume': purge_volume,
                    'purge_rate': purge_rate,
                    'purge_accel': purge_accel,
                    'switch_with_sample': switch_with_sample,
                    }

                self._abort_switch.clear()
                self._switching_flow_path = True
                self._monitor_switch_evt.set()

                logger.info(('HPLC %s starting to switch active flow '
                    'path to %s'), self.name, flow_path)

    def _monitor_switch(self):
        while not self._terminate_monitor_switch.is_set():
            self._monitor_switch_evt.wait()

            if (self._abort_switch.is_set()
                and self._terminate_monitor_switch.is_set()):
                break

            flow_path = self._switch_args['flow_path']
            stop_flow1 = self._switch_args['stop_flow1']
            stop_flow2 = self._switch_args['stop_flow2']
            restore_flow_after_switch = self._switch_args['restore_flow_after_switch']
            purge_active = self._switch_args['purge_active']
            purge_volume = self._switch_args['purge_volume']
            purge_rate = self._switch_args['purge_rate']
            purge_accel = self._switch_args['purge_accel']
            switch_with_sample = self._switch_args['switch_with_sample']

            initial_flow1 = self.get_flow_rate(self._pump1_id)
            initial_flow2 = self.get_flow_rate(self._pump2_id)

            if not self._abort_switch.is_set():
                if stop_flow1:
                    self.set_flow_rate(0, self._pump1_id)

                if stop_flow2:
                    self.set_flow_rate(0, self._pump2_id)

            if stop_flow1 or stop_flow2:
                stopped1 = not stop_flow1
                stopped2 = not stop_flow2

                while not stopped1 or not stopped2:
                    if self._abort_switch.is_set():
                        break

                    if not stopped1:
                        flow_rate1 = self.get_flow_rate(self._pump1_id)

                        if float(flow_rate1) == 0:
                            stopped1 = True

                    if not stopped2:
                        flow_rate2 = self.get_flow_rate(self._pump2_id)

                        if float(flow_rate2) == 0:
                            stopped2 = True

                    time.sleep(0.1)

            if not self._abort_switch.is_set():
                for name, pos in self._flow_path_positions[flow_path].items():
                    current_pos = int(self.get_valve_position(name))

                    if current_pos != pos:
                        self.set_valve_position(name, pos)

                    self._active_flow_path = flow_path


                logger.info(('HPLC %s switched active flow path to %s'),
                    self.name, flow_path)

                if purge_active:
                    if flow_path == 1:
                        stop_before_purge = stop_flow1
                        stop_after_purge = stop_flow1
                    elif flow_path == 2:
                        stop_before_purge = stop_flow2
                        stop_after_purge = stop_flow2

                    self.purge_flow_path(flow_path, purge_volume, purge_rate,
                        purge_accel, True, switch_with_sample, stop_before_purge,
                        stop_after_purge)

                    if restore_flow_after_switch:
                        if flow_path == 1:
                            self._pre_purge_flow1 = initial_flow1
                            self.set_flow_rate(initial_flow2, self._pump2_id)

                        elif flow_path == 2:
                            self._pre_purge_flow2 = initial_flow2
                            self.set_flow_rate(initial_flow1, self._pump1_id)

                elif restore_flow_after_switch:
                    self.set_flow_rate(initial_flow1, self._pump1_id)
                    self.set_flow_rate(initial_flow2, self._pump2_id)

            self._switching_flow_path = False
            self._monitor_switch_evt.clear()

    def set_hplc_flow_rate(self, flow_rate, flow_path):
        """
        Sets the flow rate on the specified flow path.

        Parameters
        ----------
        flow_rate: float
            The flow rate to set
        flow_path: int
            The flow path to stop the purge on. Either 1 or 2.
        """
        flow_path = int(flow_path)

        if flow_path == 1:
            self.set_flow_rate(flow_rate, self._pump1_id)
        elif flow_path == 2:
            self.set_flow_rate(flow_rate, self._pump2_id)

    def set_hplc_flow_accel(self, flow_accel, flow_path):
        """
        Sets the flow acceleration on the specified flow path.

        Parameters
        ----------
        flow_accel: float
            The flow acceleration to set
        flow_path: int
            The flow path to stop the purge on. Either 1 or 2.
        """
        flow_path = int(flow_path)

        if flow_path == 1:
            self.set_flow_accel(flow_accel, self._pump1_id)
        elif flow_path == 2:
            self.set_flow_accel(flow_accel, self._pump2_id)

    def stop_purge(self, flow_path):
        """
        Stops the purge on the specified flow path

        Parameters
        ----------
        flow_path: int
            The flow path to stop the purge on. Either 1 or 2.
        """
        flow_path = int(flow_path)

        if flow_path == 1:
            if self._purging_flow1:
                self._remaining_purge1_vol = 0

        if flow_path == 2:
            if self._purging_flow2:
                self._remaining_purge2_vol = 0

    def stop_switch(self):
        """
        Stops switching active flow path
        """
        self._abort_switch.set()
        logger.info('HPLC %s stoping switching of active flow path', self.name)

    def disconnect_all(self):
        """
        Use this method instead of disconnect to disconnect from both the
        valves and the HPLC.
        """
        for valve in self._valves.values():
            valve.disconnect()

        self._terminate_monitor_purge.set()
        self._monitor_purge_evt.set()
        self._monitor_purge_thread.join()

        self._abort_switch.set()
        self._terminate_monitor_switch.set()
        self._monitor_switch_evt.set()
        self._monitor_switch_thread.join()

        self.disconnect()



if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.INFO)
    # h1.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    hplc_args = {
        'name'  : 'SEC-SAXS',
        'args'  : ['AgilentHPLC', 'net.pipe://localhost/Agilent/OpenLAB/'],
        'kwargs': {'instrument_name': 'SEC-SAXS', 'project_name': 'Demo',
                    'get_inst_method_on_start': True}
        }

    selector_valve_args = {
        'name'  : 'Selector',
        'args'  : ['Cheminert', 'COM5'],
        'kwargs': {'positions' : 2}
        }

    outlet_valve_args = {
        'name'  : 'Outlet',
        'args'  : ['Cheminert', 'COM8'],
        'kwargs': {'positions' : 2}
        }

    purge1_valve_args = {
        'name'  : 'Purge 1',
        'args'  : ['Cheminert', 'COM7'],
        'kwargs': {'positions' : 4}
        }

    purge2_valve_args = {
        'name'  : 'Purge 2',
        'args'  : ['Cheminert', 'COM6'],
        'kwargs': {'positions' : 4}
        }

    my_hplc = AgilentHPLC2Pumps(hplc_args['name'], None, hplc_args=hplc_args,
        selector_valve_args=selector_valve_args,
        outlet_valve_args=outlet_valve_args,
        purge1_valve_args=purge1_valve_args,
        purge2_valve_args=purge2_valve_args, pump1_id='quat. pump 1#1c#1',
        pump2_id='quat. pump 2#1c#2')
