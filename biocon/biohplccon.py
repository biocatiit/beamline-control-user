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
try:
    from agilentcon.hplccon import AgilentHPLC
except Exception:
    #Not running on a computer that can talk directly to the agilent devices
    AgilentHPLC = object


class BufferMonitor(object):
    """
    Class for monitoring buffer levels. This is designed as an addon for an
    hplc class, and requires methods for getting the flow rate to be defined
    elsewhere.
    """
    def __init__(self, flow_rate_getter):
        """
        Initializes the buffer monitor class

        Parameters
        ----------
        flow_rate_getter: func
            A function that returns the flow rate of interest for monitoring.
        """
        self._get_buffer_flow_rate = flow_rate_getter

        self._active_buffer_position = None
        self._previous_flow_rate = None
        self._buffers = {}

        self._buffer_lock = threading.Lock()
        self._terminate_buffer_monitor = threading.Event()
        self._buffer_monitor_thread = threading.Thread(target=self._buffer_monitor)
        self._buffer_monitor_thread.daemon = True
        self._buffer_monitor_thread.start()

    def _buffer_monitor(self):
        while not self._terminate_buffer_monitor.is_set():
            with self._buffer_lock:
                if self._active_buffer_position is not None:
                    if self._previous_flow_rate is None:
                        self._previous_flow_rate = self._get_buffer_flow_rate()
                        previous_time = time.time()

                    current_flow = self._get_buffer_flow_rate()
                    current_time = time.time()

                    delta_vol = (((current_flow + self._previous_flow_rate)/2./60.)
                        *(current_time-previous_time))

                    if self._active_buffer_position in self._buffers:
                        self._buffers[self._active_buffer_position]['vol'] -= delta_vol

                    self._previous_flow_rate = current_flow
                    previous_time = current_time

            time.sleep(0.1)

    def get_buffer_info(self, position):
        """
        Gets the buffer info including the current volume

        Parameters
        ----------
        position: str
            The buffer position to get the info for.

        Returns
        -------
        vol: float
            The volume remaining
        descrip: str
            The buffer description (e.g. contents)
        """
        with self._buffer_lock:
            position = str(position)
            vals = self._buffers[position]
            vol = vals['vol']
            descrip = vals['descrip']

        return vol, descrip

    def get_all_buffer_info(self):
        """
        Gets information on all buffers

        Returns
        -------
        buffers: dict
            A dictionary where the keys are the buffer positions and
            the values are dictionarys with keys for volume ('vol') and
            description ('descrip').
        """
        with self._buffer_lock:
            buffers = copy.copy(self._buffers)
        return buffers

    def set_buffer_info(self, position, volume, descrip):
        """
        Sets the buffer info for a given buffer position

        Parameters
        ----------
        position: str
            The buffer position (e.g. 1 or A or etc)
        volume: float
            The current buffer volume
        descrip: str
            Buffer description (e.g. contents)
        """
        with self._buffer_lock:
            position = str(position)
            self._buffers[position] = {'vol': float(volume), 'descrip': descrip}

    def set_active_buffer_position(self, position):
        """
        Sets the active buffer position

        Parameters
        ----------
        position: str
            The buffer position (e.g. 1 or A)
        """
        with self._buffer_lock:
            self._active_buffer_position = str(position)
            self._previous_flow_rate = None

    def stop_monitor(self):
        self._terminate_buffer_monitor.set()
        self._buffer_monitor_thread.join()

class AgilentHPLC2Pumps(AgilentHPLC):
    """
    Specific control for the SEC-SAXS Agilent HPLC with dual pumps
    """

    def __init__(self, name, device, hplc_args={}, selector_valve_args={},
        outlet_valve_args={}, purge1_valve_args={}, purge2_valve_args={},
        buffer1_valve_args={}, buffer2_valve_args={}, pump1_id='', pump2_id=''):
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
        buffer1_valve_args: dict
            Dictionary of the input arguments for the flowpath 1 buffer valve
        buffer2_valve_args: dict
            Dictionary of the input arguments for the flowpath 2 buffer valve
        pump1_id: str
            The Agilent hashkey for pump 1
        pump2_id: str
            The Agilent hashkey for pump 2
        """
        self._active_flow_path = None
        self._purging_flow1 = False
        self._purging_flow2 = False
        self._equil_flow1 = False
        self._equil_flow2 = False

        self._buffer_monitor1 = BufferMonitor(self._get_flow_rate1)
        self._buffer_monitor2 = BufferMonitor(self._get_flow_rate2)

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
            purge1_valve_args, purge2_valve_args, buffer1_valve_args,
            buffer2_valve_args)

        # Connect HPLC
        self._pump1_id = pump1_id
        self._pump2_id = pump2_id

        hplc_device_type = hplc_args['args'][0]
        hplc_device = hplc_args['args'][1]
        hplc_kwargs = hplc_args['kwargs']

        AgilentHPLC.__init__(self, name, hplc_device, **hplc_kwargs)

        while not self.get_connected():
            time.sleep(0.1)

        # Other definitions
        self._default_purge_rate = 5.0 #mL/min
        self._default_purge_accel = 10.0 #mL/min
        self._pre_purge_flow1 = None
        self._pre_purge_flow2 = None
        self._pre_purge_flow_accel1 = 0.0
        self._pre_purge_flow_accel2 = 0.0
        self._remaining_purge1_vol = 0.0
        self._remaining_purge2_vol = 0.0
        self._target_purge_flow1 = 0.0
        self._target_purge_flow2 = 0.0
        self._target_purge_accel1 = 0.0
        self._target_purge_accel2 = 0.0
        self._stop_before_purging1 = True
        self._stop_before_purging2 = True

        self._purge1_ongoing = threading.Event()
        self._purge2_ongoing = threading.Event()

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

        self._submitting_sample = False

        self._monitor_submit_evt = threading.Event()
        self._terminate_monitor_submit = threading.Event()
        self._abort_submit = threading.Event()
        self._monitor_submit_thread = threading.Thread(
            target=self._monitor_submit)
        self._monitor_submit_thread.daemon = True
        self._monitor_submit_thread.start()

        self._remaining_equil1_vol = 0.0
        self._remaining_equil2_vol = 0.0

        self._abort_equil1 = threading.Event()
        self._abort_equil2 = threading.Event()
        self._monitor_equil_evt = threading.Event()
        self._terminate_monitor_equil = threading.Event()
        self._monitor_equil_thread = threading.Thread(
            target=self._monitor_equil)
        self._monitor_equil_thread.daemon = True
        self._monitor_equil_thread.start()


        self.set_active_buffer_position(self.get_valve_position('buffer1'), 1)
        # self.set_active_buffer_position(self.get_valve_position('buffer2'), 2)

    def  connect(self):
        """
        Expected by the thread, but connection is don on init, so this does nothing
        """
        pass

    def _connect_valves(self, sv_args, ov_args, p1_args, p2_args, b1_args,
        b2_args):
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

        b1_name = b1_args['name']
        b1_arg_list = b1_args['args']
        b1_kwarg_list = b1_args['kwargs']
        b1_device_type = b1_arg_list[0]
        b1_comm = b1_arg_list[1]

        self._buffer1_valve = valvecon.known_valves[b1_device_type](b1_name,
            b1_comm, **b1_kwarg_list)
        self._buffer1_valve.connect()

        b2_name = b2_args['name']
        b2_arg_list = b2_args['args']
        b2_kwarg_list = b2_args['kwargs']
        b2_device_type = b2_arg_list[0]
        b2_comm = b2_arg_list[1]

        self._buffer2_valve = valvecon.known_valves[b2_device_type](b2_name,
            b2_comm, **b2_kwarg_list)
        self._buffer2_valve.connect()

        self._valves = {
            'selector'  : self._selector_valve,
            'outlet'    : self._outlet_valve,
            'purge1'    : self._purge1_valve,
            'purge2'    : self._purge2_valve,
            'buffer1'   : self._buffer1_valve,
            'buffer2'   : self._buffer2_valve,
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

        for flow_path in self._purge_positions:
            purging = True

            for valve, fp_pos in self._purge_positions[flow_path].items():
                current_pos = self.get_valve_position(valve)

                if int(fp_pos) != int(current_pos):
                    purging = False
                    break

            if purging:
                if flow_path == 1:
                    self._purging_flow1 = True
                elif flow_path == 2:
                    self._purging_flow2 = True

    def get_valve_position(self, valve_id):
        """
        Gets the position of the specified valve.

        Parameters
        ----------
        valve_id: str
            Valve name. Can be selector, outlet, purge1, purge2, buffer1, buffer2

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
        flow_path = copy.copy(self._active_flow_path)
        return flow_path

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

    def get_equilibration_status(self, flow_path):
        """
        Gets the equilibration status of the specified flow path.

        Parameters
        ----------
        flow_path: int
            The flow path to get the status for. Either 1 or 2.

        Returns
        -------
        is_equilibrating: bool
            True if the flow path is equilibrating, False if not.
        remaining_volume: float
            The remaining volume to equilibrate.
        """
        flow_path = int(flow_path)

        if flow_path == 1:
            is_equilibrating = copy.copy(self._equil_flow1)
            remaining_volume = copy.copy(self._remaining_equil1_vol)

        elif flow_path == 2:
            is_equilibrating = copy.copy(self._equil_flow2)
            remaining_volume = copy.copy(self._remaining_equil2_vol)

        return is_equilibrating, remaining_volume

    def get_hplc_flow_rate(self, flow_path):
        """
        Gets the flow rate of the specified flow path

        Parameters
        ----------
        flow_path: int
            The flow path to get the rate for. Either 1 or 2.

        Returns
        -------
        flow_rate: float
            The flow rate of the specified flow path.
        """
        flow_path = int(flow_path)

        if flow_path == 1:
            flow_rate = self.get_data_trace('Quat. Pump 1: Flow (mL/min)')[1][-1]
        elif flow_path == 2:
            flow_rate = self.get_data_trace('Quat. Pump 2: Flow (mL/min)')[1][-1]

        return float(flow_rate)

    def get_hplc_target_flow_rate(self, flow_path, update_method=True):
        """
        Gets the target flow rate of the specified flow path

        Parameters
        ----------
        flow_path: int
            The flow path to get the rate for. Either 1 or 2.
        update_method: bool
            If true, get the current method from instrument. If doing multiple
            things that use the current method status in a row, it can be useful
            to set this to false for some cases, may be faster.

        Returns
        -------
        target_flow_rate: float
            The flow rate of the specified flow path.
        """
        flow_path = int(flow_path)

        if flow_path == 1:
            target_flow_rate = self.get_target_flow_rate(self._pump1_id,
                update_method)
        elif flow_path == 2:
            target_flow_rate = self.get_target_flow_rate(self._pump2_id,
                update_method)

        return target_flow_rate

    def get_hplc_flow_accel(self, flow_path, update_method=True):
        """
        Gets the flow acceleration of the specified flow path

        Parameters
        ----------
        flow_path: int
            The flow path to get the acceleration for. Either 1 or 2.
        update_method: bool
            If true, get the current method from instrument. If doing multiple
            things that use the current method status in a row, it can be useful
            to set this to false for some cases, may be faster.

        Returns
        -------
        flow_accel: float
            The flow acceleration of the specified flow path.
        """
        flow_path = int(flow_path)

        if flow_path == 1:
            flow_accel = self.get_flow_accel(self._pump1_id, update_method)
        elif flow_path == 2:
            flow_accel = self.get_flow_accel(self._pump2_id, update_method)

        return flow_accel

    def get_hplc_pressure(self, flow_path):
        """
        Gets the pump pressure of the specified flow path

        Parameters
        ----------
        flow_path: int
            The flow path to get the pressure for. Either 1 or 2.

        Returns
        -------
        pressure: float
            The pump pressure of the specified flow path.
        """
        flow_path = int(flow_path)

        if flow_path == 1:
            pressure = self.get_data_trace('Quat. Pump 1: Pressure (bar)')[1][-1]
        elif flow_path == 2:
            pressure = self.get_data_trace('Quat. Pump 2: Pressure (bar)')[1][-1]

        return pressure

    def get_hplc_high_pressure_limit(self, flow_path, update_method=True):
        """
        Gets the pump high pressure limit of the specified flow path

        Parameters
        ----------
        flow_path: int
            The flow path to get the high pressure limit for. Either 1 or 2.
         update_method: bool
            If true, get the current method from instrument. If doing multiple
            things that use the current method status in a row, it can be useful
            to set this to false for some cases, may be faster.

        Returns
        -------
        pressure: float
            The pump high pressure limit of the specified flow path.
        """
        flow_path = int(flow_path)

        if flow_path == 1:
            pressure = self.get_high_pressure_limit(self._pump1_id,
                update_method)
        elif flow_path == 2:
            pressure = self.get_high_pressure_limit(self._pump2_id,
                update_method)

        return pressure

    def get_hplc_pump_power_status(self, flow_path):
        """
        Gets the pump power status of the specified flow path

        Parameters
        ----------
        flow_path: int
            The flow path to get the pump power status for. Either 1 or 2.

        Returns
        -------
        pump_power_status: float
            The pump power status of the specified flow path. Either 'On',
            'Off', or 'Standby'. Returns an empty string if status cannot
            be acquired.
        """
        flow_path = int(flow_path)

        if flow_path == 1:
            pump_power_status = self.get_pump_power_status(self._pump1_id)
        elif flow_path == 2:
            pump_power_status = self.get_pump_power_status(self._pump2_id)

        return pump_power_status

    def get_hplc_autosampler_temperature(self):
        """
        Gets the autosampler temperature

        Returns
        -------
        temperature: float
            The autosampler temperature
        """
        temperature = self.get_data_trace('Multisampler: Temperature (°C)')[1][-1]
        return float(temperature)

    def _get_flow_rate1(self):
        return self.get_hplc_flow_rate(1)

    def _get_flow_rate2(self):
        return self.get_hplc_flow_rate(2)

    def get_flow_path_switch_status(self):
        """
        Gets whether or not the HPLC is currently switching flow paths.

        Returns
        -------
        is_switching: bool
            True if switching, otherwise False.
        """
        switching = copy.copy(self._switching_flow_path)
        return switching

    def get_submitting_sample_status(self):
        """
        Gets whether or not the HPLC is submitting a sample.

        Returns
        -------
        is_submitting: bool
            True if submitting, otherwise False
        """
        submitting = copy.copy(self._submitting_sample)
        return submitting

    def get_buffer_info(self, position, flow_path):
        """
        Gets the buffer info including the current volume

        Parameters
        ----------
        position: str
            The buffer position to get the info for.
        flow_path: int
            The flow path to get the info for. Either 1 or 2.

        Returns
        -------
        vol: float
            The volume remaining
        descrip: str
            The buffer description (e.g. contents)
        """
        flow_path = int(flow_path)

        if flow_path == 1:
            vol, descrip = self._buffer_monitor1.get_buffer_info(position)
        elif flow_path == 2:
            vol, descrip = self._buffer_monitor2.get_buffer_info(position)

        return vol, descrip

    def get_all_buffer_info(self, flow_path):
        """
        Gets information on all buffers

        Parameters
        ----------
        flow_path: int
            The flow path to get the info for. Either 1 or 2.

        Returns
        -------
        buffers: dict
            A dictionary where the keys are the buffer positions and
            the values are dictionarys with keys for volume ('vol') and
            description ('descrip').
        """
        flow_path = int(flow_path)

        if flow_path == 1:
            buffers = self._buffer_monitor1.get_all_buffer_info()
        elif flow_path == 2:
            buffers = self._buffer_monitor2.get_all_buffer_info()

        return buffers

    def equilibrate_flow_path(self, flow_path, equil_volume, equil_rate,
        equil_accel, purge=True, purge_volume=20, purge_rate=None, purge_accel=None,
        equil_with_sample=False, stop_after_equil=True):
        """
        Equilibrate the specified flow path. Note that attempting to start
        equilibrating a flow path that is already equilibrating or purging
        will result in an error

        Parameters
        ----------
        flow_path: int
            The flow path to purge. Either 1 or 2.
        equil_volume: float
            Volume in mL to be equilibrated (doesn't include any purge volume)
        equil_rate: float
            Flow rate to use for equilibration
        equil_accel: float
            Flow acceleration to be use dfor equilibration
        purge: bool
            If true, a pump purge is done before the equilibration.
        purge_volume: float
            Volume in mL to be purged.
        purge_rate: float
            Flow rate to use for purging. If no rate supplied, the device's
            default purge rate is used.
        purge_accel: float
            Flow acceleration to use for purging. If no rate is supplied, the
            device's default purge rate is used.
        equil_with_sample: bool
            Checks whether there are samples in the run queue. If there are, and
            the run queue is not paused and you are equilibrating the active flow
            path, you must pass True for this value to carry out the
            equilbiration. Otherwise the equilibration will not run.
        stop_after_equil: bool
            Whether or not the flow should be stopped after the equilibration
            is done.
        """
        flow_path = int(flow_path)

        if ((flow_path == 1 and (self._purging_flow1 or self._equil_flow1)) or
            (flow_path == 2 and (self._purging_flow2 or self._equil_flow2))):
            logger.error('HPLC %s flow path %s is already equilibrating or '
                'puring, so a new equilibration cannot be started', self.name,
                flow_path)
            success = False

        else:
            do_equil = self._check_equil_sample_status(flow_path,
                equil_with_sample)

            if do_equil:
                self._start_equil(flow_path, equil_volume, equil_rate,
                    equil_accel, purge, purge_volume, purge_rate, purge_accel,
                    equil_with_sample, stop_after_equil)

                success =  True

            else:
                success = False

        return success

    def _check_equil_sample_status(self, flow_path, equil_with_sample):
        do_equil = True

        if self._active_flow_path == flow_path:
            samples_being_run = self._check_samples_being_run()

            if samples_being_run and not equil_with_sample:
                logger.error(('HPLC %s cannot equilibrate flow path %s because '
                    'samples are being run.'), self.name, flow_path)
                do_equil = False

        return do_equil

    def _start_equil(self, flow_path, equil_volume, equil_rate, equil_accel,
        purge, purge_volume, purge_rate, purge_accel, equil_with_sample,
        stop_after_equil):
        if purge_rate is None:
            purge_rate = self._default_purge_rate
        if purge_accel is None:
            purge_accel = self._default_purge_accel

        if flow_path == 1:
            self._equil1_args = {
                'equil_rate'    : equil_rate,
                'equil_accel'   : equil_accel,
                'purge'         : purge,
                'purge_volume'  : purge_volume,
                'purge_rate'    : purge_rate,
                'purge_accel'   : purge_accel,
                'stop_after_equil'  : stop_after_equil,
                }

            self._remaining_equil1_vol = equil_volume
            self._equil_flow1 = True
            self._abort_equil1.clear()

        elif flow_path == 2:
            self._equil2_args = {
                'equil_rate'    : equil_rate,
                'equil_accel'   : equil_accel,
                'purge'         : purge,
                'purge_volume'  : purge_volume,
                'purge_rate'    : purge_rate,
                'purge_accel'   : purge_accel,
                'stop_after_equil'  : stop_after_equil,
                }

            self._remaining_equil2_vol = equil_volume
            self._equil_flow2 = True
            self._abort_equil2.clear()

        self._monitor_equil_evt.set()

        logger.info(('HPLC %s started equilibration of flow path %s for %s mL'),
            self.name, flow_path, equil_volume)

    def _monitor_equil(self):
        start_purge1 = False
        start_purge2 = False
        monitor_purge1 = False
        monitor_purge2 = False
        run_flow1 = False
        run_flow2 = False
        stopping_flow1 = False
        stopping_flow2 = False

        while not self._terminate_monitor_equil.is_set():
            self._monitor_equil_evt.wait()

            if (self._equil_flow1 and not start_purge1 and not monitor_purge1
                and not run_flow1 and not stopping_flow1):
                start_purge1 = True
                monitor_purge1 = False
                run_flow1 = False
                stopping_flow1 = False

                equil_rate1 = self._equil1_args['equil_rate']
                equil_accel1 = self._equil1_args['equil_accel']
                purge1 = self._equil1_args['purge']
                purge_volume1 = self._equil1_args['purge_volume']
                purge_rate1 = self._equil1_args['purge_rate']
                purge_accel1 = self._equil1_args['purge_accel']
                stop_after_equil1 = self._equil1_args['stop_after_equil']

                self.set_hplc_flow_accel(equil_accel1, 1)

            if (self._equil_flow2 and not start_purge2 and not monitor_purge2
                and not run_flow2 and not stopping_flow2):
                start_purge2 = True
                monitor_purge2 = False
                run_flow2 = False
                stopping_flow2 = False

                equil_rate2 = self._equil2_args['equil_rate']
                equil_accel2 = self._equil2_args['equil_accel']
                purge2 = self._equil2_args['purge']
                purge_volume2 = self._equil2_args['purge_volume']
                purge_rate2 = self._equil2_args['purge_rate']
                purge_accel2 = self._equil2_args['purge_accel']
                stop_after_equil2 = self._equil2_args['stop_after_equil']

                self.set_hplc_flow_accel(equil_accel2, 2)


            if start_purge1:
                if purge1:
                    self.purge_flow_path(1, purge_volume1, purge_rate1,
                        purge_accel1, False, True)

                    while not self._purge1_ongoing.is_set():
                        time.sleep(0.1)

                start_purge1 = False
                monitor_purge1 = True

            if start_purge2:
                if purge2:
                    self.purge_flow_path(2, purge_volume2, purge_rate2,
                        purge_accel2, False, True)

                    while not self._purge2_ongoing.is_set():
                        time.sleep(0.1)

                start_purge2 = False
                monitor_purge2 = True


            if monitor_purge1:
                if not self._purge1_ongoing.is_set():
                    monitor_purge1 = False
                    run_flow1 = True

                    if not self._abort_equil1.is_set():
                        self.set_hplc_flow_rate(equil_rate1, 1)
                        previous_flow1 = self.get_hplc_flow_rate(1)
                        previous_time1 = time.time()
                    else:
                        previous_flow1 = 0
                        previous_time1 = time.time()

            if monitor_purge2:
                if not self._purge2_ongoing.is_set():
                    monitor_purge2 = False
                    run_flow2 = True

                    if not self._abort_equil2.is_set():
                        self.set_hplc_flow_rate(equil_rate2, 2)
                        previous_flow2 = self.get_hplc_flow_rate(2)
                        previous_time2 = time.time()
                    else:
                        previous_flow2 = 0
                        previous_time2 = time.time()


            if run_flow1:
                current_flow1 = self.get_hplc_flow_rate(1)
                current_time1 = time.time()
                delta_vol1 = (((current_flow1 + previous_flow1)/2./60.)
                    *(current_time1-previous_time1))

                self._remaining_equil1_vol -= delta_vol1

                if equil_accel1 > 0 and stop_after_equil1:
                    stop_vol1 = (current_flow1/equil_accel1)*(current_flow1/2.)
                else:
                    stop_vol1 = 0

                previous_time1 = current_time1
                previous_flow1 = current_flow1

                if self._remaining_equil1_vol - stop_vol1 <= 0:
                    run_flow1 = False

                    if stop_after_equil1:
                        self.set_hplc_flow_rate(0, 1)

                    stopping_flow1 = True
                    run_flow1 = False

            if run_flow2:
                current_flow2 = self.get_hplc_flow_rate(2)
                current_time2 = time.time()
                delta_vol2 = (((current_flow2 + previous_flow2)/2./60.)
                    *(current_time2-previous_time2))

                self._remaining_equil2_vol -= delta_vol2

                if equil_accel2 > 0 and stop_after_equil2:
                    stop_vol2 = (current_flow2/equil_accel2)*(current_flow2/2.)
                else:
                    stop_vol2 = 0

                previous_time2 = current_time2
                previous_flow2 = current_flow2

                if self._remaining_equil2_vol - stop_vol2 <= 0:
                    run_flow2 = False

                    if stop_after_equil2:
                        self.set_hplc_flow_rate(0, 2)

                    stopping_flow2 = True
                    run_flow2 = False


            if stopping_flow1:
                current_flow1 = self.get_hplc_flow_rate(1)

                if ((stop_after_equil1 and current_flow1 == 0)
                    or not stop_after_equil1):

                    stopping_flow1 = False
                    self._equil_flow1 = False

                    logger.info(('HPLC %s finished equilibrating flow path 1'),
                        self.name)

            if stopping_flow2:
                current_flow2 = self.get_hplc_flow_rate(2)

                if ((stop_after_equil2 and current_flow2 == 0)
                    or not stop_after_equil2):

                    stopping_flow2 = False
                    self._equil_flow2 = False

                    logger.info(('HPLC %s finished equilibrating flow path 2'),
                        self.name)


            if not self._equil_flow1 and not self._equil_flow2:
                self._monitor_equil_evt.clear()
            else:
                time.sleep(0.1)

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
            success = False

        else:
            do_purge = self._check_purge_sample_status(flow_path,
                purge_with_sample)

            if do_purge:
                self._start_purge(flow_path, purge_volume, purge_rate,
                    purge_accel, restore_flow_after_purge, stop_before_purge,
                    stop_after_purge)

                success =  True

            else:
                success = False

        return success

    def _start_purge(self, flow_path, purge_volume, purge_rate, purge_accel,
            restore_flow_after_purge, stop_before_purge, stop_after_purge):
        if purge_rate is None:
            purge_rate = self._default_purge_rate
        if purge_accel is None:
            purge_accel = self._default_purge_accel

        if flow_path == 1:
            if restore_flow_after_purge:
                self._pre_purge_flow1 = self.get_hplc_flow_rate(1)
            else:
                self._pre_purge_flow1 = None

            self._pre_purge_flow_accel1 = self.get_flow_accel(self._pump1_id)
            self._remaining_purge1_vol = purge_volume
            self._target_purge_flow1 = purge_rate
            self._target_purge_accel1 = purge_accel
            self._stop_before_purging1 = stop_before_purge
            self._stop_after_purging1 = stop_after_purge
            self._purging_flow1 = True
            self._purge1_ongoing.set()

        elif flow_path == 2:
            if restore_flow_after_purge:
                self._pre_purge_flow2 = self.get_hplc_flow_rate(2)
            else:
                self._pre_purge_flow2 = None

            self._pre_purge_flow_accel2 = self.get_flow_accel(self._pump2_id)
            self._remaining_purge2_vol = purge_volume
            self._target_purge_flow2 = purge_rate
            self._target_purge_accel2 = purge_accel
            self._stop_before_purging2 = stop_before_purge
            self._stop_after_purging2 = stop_after_purge
            self._purging_flow2 = True
            self._purge2_ongoing.set()

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

            if self._terminate_monitor_purge.is_set():
                break

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
                    current_flow1 = self.get_hplc_flow_rate(1)

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
                    previous_flow1 = self.get_hplc_flow_rate(1)
                    previous_time1 = time.time()
                    update_time1 = previous_time1

                    self.set_flow_rate(self._target_purge_flow1, self._pump1_id)

                    stopping_initial_flow1 = False
                    monitoring_flow1 = True

                    if self._pre_purge_flow1 is None:
                        final_flow1 = 0
                    else:
                        final_flow1 = self._pre_purge_flow1

            if stopping_initial_flow2:
                if self._stop_before_purging2:
                    current_flow2 = self.get_hplc_flow_rate(2)

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
                    previous_flow2 = self.get_hplc_flow_rate(2)
                    previous_time2 = time.time()
                    update_time2 = previous_time2

                    self.set_flow_rate(self._target_purge_flow2, self._pump2_id)

                    stopping_initial_flow2 = False
                    monitoring_flow2 = True

                    if self._pre_purge_flow2 is None:
                        final_flow2 = 0
                    else:
                        final_flow2 = self._pre_purge_flow2


            if monitoring_flow1:
                current_flow1 = self.get_hplc_flow_rate(1)
                current_time1 = time.time()
                delta_vol1 = (((current_flow1 + previous_flow1)/2./60.)
                    *(current_time1-previous_time1))

                self._remaining_purge1_vol -= delta_vol1

                if flow_accel1 > 0:
                    if self._stop_after_purging1:
                        stop_vol1 = ((current_flow1)/flow_accel1)*(current_flow1/2.)
                    else:
                        stop_vol1 = abs((current_flow1-final_flow1)/flow_accel1)*(current_flow1/2.)
                else:
                    stop_vol1 = 0

                previous_time1 = current_time1
                previous_flow1 = current_flow1

                if self._remaining_purge1_vol - stop_vol1 <= 0:
                    monitoring_flow1 = False
                    stopping_flow1 = True

                    if self._stop_after_purging1:
                        self.set_flow_rate(0, self._pump1_id)
                    else:
                        self.set_flow_rate(final_flow1, self._pump1_id)

                if current_time1 - update_time1 > 15:
                    update_time1 = current_time1

            if monitoring_flow2:
                current_flow2 = self.get_hplc_flow_rate(2)
                current_time2 = time.time()
                delta_vol2 = (((current_flow2 + previous_flow2)/2./60.)
                    *(current_time2-previous_time2))

                self._remaining_purge2_vol -= delta_vol2

                if flow_accel2 > 0:
                    if self._stop_after_purging2:
                        stop_vol2 = ((current_flow2)/flow_accel2)*(current_flow2/2.)
                    else:
                        stop_vol2 = abs((current_flow2-final_flow2)/flow_accel2)*(current_flow2/2.)
                else:
                    stop_vol2 = 0

                previous_time2 = current_time2
                previous_flow2 = current_flow2

                if self._remaining_purge2_vol - stop_vol2 <= 0:
                    monitoring_flow2 = False
                    stopping_flow2 = True

                    if self._stop_after_purging2:
                        self.set_flow_rate(0, self._pump2_id)
                    else:
                        self.set_flow_rate(final_flow2, self._pump2_id)

                if current_time2 - update_time2 > 15:
                    update_time2 = current_time2


            if stopping_flow1:
                current_flow1 = self.get_hplc_flow_rate(1)
                current_time1 = time.time()

                if ((self._stop_after_purging1 and current_flow1 == 0)
                    or (not self._stop_after_purging1
                    and current_flow1 == final_flow1)):
                    self.set_flow_accel(self._pre_purge_flow_accel1,
                        self._pump1_id)

                    for name, pos in self._column_positions[1].items():
                        current_pos = int(self.get_valve_position(name))

                        if current_pos != pos:
                            self.set_valve_position(name, pos)

                    if self._stop_after_purging1:
                        self.set_flow_rate(final_flow1, self._pump1_id)

                    stopping_flow1 = False
                    self._purging_flow1 = False
                    self._purge1_ongoing.clear()

                    logger.info(('HPLC %s finished purging flow path 1. '
                        'Flow rate set to %s'), self.name, final_flow1)

                if current_time1 - update_time1 > 15:
                    update_time1 = current_time1

            if stopping_flow2:
                current_flow2 = self.get_hplc_flow_rate(2)
                current_time2 = time.time()

                if ((self._stop_after_purging2 and current_flow2 == 0)
                    or (not self._stop_after_purging2
                    and current_flow2 == final_flow2)):
                    self.set_flow_accel(self._pre_purge_flow_accel2,
                        self._pump2_id)

                    for name, pos in self._column_positions[2].items():
                        current_pos = int(self.get_valve_position(name))

                        if current_pos != pos:
                            self.set_valve_position(name, pos)

                    if self._stop_after_purging2:
                        self.set_flow_rate(final_flow2, self._pump2_id)

                    stopping_flow2 = False
                    self._purging_flow2 = False
                    self._purge2_ongoing.clear()

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

        if valve_id == 'buffer1':
            self.set_active_buffer_position(position, 1)
        elif valve_id == 'buffer2':
            self.set_active_buffer_position(position, 2)
        elif valve_id == 'purge1':
            if position == self._purge_positions[1]['purge1']:
                self._purging_flow1 = True
            else:
                self._purging_flow1 = False
        elif valve_id == 'purge2':
            if position == self._purge_positions[2]['purge2']:
                self._purging_flow2 = True
            else:
                self._purging_flow2 = False

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

        success = True

        if self._active_flow_path == flow_path:
            logger.info('HPLC %s already set to active flow path %s',
                self.name, flow_path)
            success = False
        elif self._switching_flow_path:
            logger.error('HPLC %s cannot switch flow paths because a switch '
                'is already underway.', self.name)
            success = False
        else:
            samples_being_run = self._check_samples_being_run()

            if samples_being_run and not switch_with_sample:
                logger.error(('HPLC %s cannot switch active flow path because '
                    'samples are being run'), self.name)
                success = False

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

        return success

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

            initial_flow1 = self.get_hplc_flow_rate(1)
            initial_flow2 = self.get_hplc_flow_rate(2)

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
                        flow_rate1 = self.get_hplc_flow_rate(1)

                        if float(flow_rate1) == 0:
                            stopped1 = True

                    if not stopped2:
                        flow_rate2 = self.get_hplc_flow_rate(2)

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

        Returns
        -------
        success: bool
            True if successful.
        """
        flow_path = int(flow_path)
        flow_rate = float(flow_rate)

        if flow_path == 1:
            pump_id = self._pump1_id
        elif flow_path == 2:
            pump_id = self._pump2_id

        success = self.set_flow_rate(flow_rate, pump_id)

        # run_queue = self.get_run_queue()

        # all_methods = []
        # for run in run_queue:
        #     name = run[0]
        #     run_data = self.get_run_data(name)
        #     acq_method_list = run_data['acq_method']

        #     all_methods.extend(acq_method_list)

        # all_methods = list(set(all_methods))

        # for method in all_methods:
        #     self.load_method(method)
        #     self.set_pump_method_values({'Flow': flow_rate}, pump_id)
        #     self.save_current_method()

        return success


    def set_hplc_flow_accel(self, flow_accel, flow_path):
        """
        Sets the flow acceleration on the specified flow path.

        Parameters
        ----------
        flow_accel: float
            The flow acceleration to set
        flow_path: int
            The flow path to stop the purge on. Either 1 or 2.

        Returns
        -------
        success: bool
            True if successful.
        """
        flow_path = int(flow_path)
        flow_accel = float(flow_accel)

        if flow_path == 1:
            pump_id = self._pump1_id
        elif flow_path == 2:
            pump_id = self._pump2_id

        success = self.set_flow_accel(flow_accel, pump_id)

        # run_queue = self.get_run_queue()

        # all_methods = []
        # for run in run_queue:
        #     name = run[0]
        #     run_data = self.get_run_data(name)
        #     acq_method_list = run_data['acq_method']

        #     all_methods.extend(acq_method_list)

        # all_methods = list(set(all_methods))

        # for method in all_methods:
        #     self.load_method(method)
        #     self.set_pump_method_values({'MaximumFlowRamp': flow_accel}, pump_id)
        #     self.save_current_method()

        return success

    def set_hplc_high_pressure_limit(self, pressure, flow_path):
        """
        Sets the flow rate on the specified flow path.

        Parameters
        ----------
        pressure: float
            The flow rate to set
        flow_path: int
            The flow path to stop the purge on. Either 1 or 2.

        Returns
        -------
        success: bool
            True if successful.
        """
        flow_path = int(flow_path)
        pressure = float(pressure)

        if flow_path == 1:
            pump_id = self._pump1_id
        elif flow_path == 2:
            pump_id = self._pump2_id

        success = self.set_high_pressure_limit(pressure, pump_id)

        return success


    def set_hplc_pump_on(self, flow_path):
        """
        Turns on the pump on the specified flow path.

        Parameters
        ----------
        flow_path: int
            The flow path to turn on the pump on. Either 1 or 2.

        Returns
        -------
        success: bool
            True if successful.
        """
        flow_path = int(flow_path)

        if flow_path == 1:
            pump_id = self._pump1_id
        elif flow_path == 2:
            pump_id = self._pump2_id

        success = self.set_pump_on(pump_id)

        return success

    def set_hplc_pump_standby(self, flow_path):
        """
        Turns the pump on the specified flow path to standby.

        Parameters
        ----------
        flow_path: int
            The flow path to set the pump to standby on. Either 1 or 2.

        Returns
        -------
        success: bool
            True if successful.
        """
        flow_path = int(flow_path)

        if flow_path == 1:
            pump_id = self._pump1_id
        elif flow_path == 2:
            pump_id = self._pump2_id

        success = self.set_pump_standby(pump_id)

        return success

    def set_buffer_info(self, position, volume, descrip, flow_path):
        """
        Sets the buffer info for a given buffer position

        Parameters
        ----------
        position: str
            The buffer position (e.g. 1 or A or etc)
        volume: float
            The current buffer volume
        descrip: str
            Buffer description (e.g. contents)
        flow_path: int
            The flow path to set the info for. Either 1 or 2.

        Returns
        -------
        success: bool
            True if successful.
        """
        flow_path = int(flow_path)

        if flow_path == 1:
            self._buffer_monitor1.set_buffer_info(position, volume,
                descrip)
        elif flow_path == 2:
            self._buffer_monitor2.set_buffer_info(position, volume,
                descrip)

        return True

    def set_active_buffer_position(self, position, flow_path):
        """
        Sets the active buffer position

        Parameters
        ----------
        position: str
            The buffer position (e.g. 1 or A)
        flow_path: int
            The flow path to set the info for. Either 1 or 2.
        """
        flow_path = int(flow_path)
        if flow_path == 1:
            self._buffer_monitor1.set_active_buffer_position(position)
        elif flow_path == 2:
            self._buffer_monitor2.set_active_buffer_position(position)

    def submit_hplc_sample(self, name, acq_method, sample_loc, inj_vol,
        flow_rate, flow_accel, total_elution_vol, high_pressure_lim,
        result_path=None, sp_method=None, wait_for_flow_ramp=True,
        settle_time=0.):
        """
        Submits a sample to the hplc run queue. Note that due to limitations
        of the run queue and how it gets method parameters you should only every
        submit one sample, and don't submit another until it's finished. Doing
        otherwise could mess up the flow on one or both of the pumps.

        Parameters
        ----------
        name: str
            The name of the sample. Used internally and as the result save name.
            Must be unique to the hplc sample list (including finished samples).
        acq_method: str
            The acquisition method name relative to the top level OpenLab
            CDS Methods folder.
        sample_loc: str
            The sample location in the autosampler (e.g. D1F-A1 being drawer
            1 front, position A1)
        inj_vol: float
            The injection volume.
        flow_rate: float
            The elution flow rate
        flow_accel: float
            The elution flow acceleration
        total_elution_vol: float
            The total elution volume. Used to calculate the method run time
            based on the provided flow rate.
        high_pressure_lim: float
            The high pressure limit for the elution run.
        result_path: str
            The path to save the result in, relative to the project base
            results path. If no path is provided, the base results path will
            be used.
        sp_method: str
            Sample prep method to be used. The path should be relative to the
            top level Methods folder.
        wait_for_flow_ramp: bool
            Whether or not the submission should wait until the flow has ramped
            up to the elution flow rate.
        settle_time: float
            Time in s to wait after the flow has ramped up before submitting
            the sample.

        Returns
        -------
        success: bool
            True if successful
        """
        flow_rate = float(flow_rate)
        flow_accel = float(flow_accel)
        high_pressure_lim = float(high_pressure_lim)
        inj_vol = float(inj_vol)

        self.set_hplc_flow_accel(flow_accel, self._active_flow_path)
        self.set_hplc_flow_rate(flow_rate, self._active_flow_path)

        if self._active_flow_path == 1:
            active_pump_id = self._pump1_id
            eq_pump_id = self._pump2_id
        elif self._active_flow_path == 2:
            active_pump_id = self._pump2_id
            eq_pump_id = self._pump1_id

        stop_time = total_elution_vol/flow_rate

        self.get_current_method_from_instrument()
        eq_pump_method_vals = self.get_pump_method_values(['Flow',
            'MaximumFlowRamp', 'HighPressureLimit'], eq_pump_id)
        eq_pump_method_vals['StopTime_Time'] = stop_time

        acq_pump_method_vals = {
            'Flow': flow_rate,
            'MaximumFlowRamp': flow_accel,
            'HighPressureLimit': high_pressure_lim,
            'StopTime_Time': stop_time,
            }

        self.load_method(acq_method)
        self.set_pump_method_values(eq_pump_method_vals, eq_pump_id)
        self.set_pump_method_values(acq_pump_method_vals, active_pump_id)
        self.save_current_method()

        sequence_vals = {
            'acq_method'    : acq_method,
            'sample_loc'    : sample_loc,
            'injection_vol' : inj_vol,
            'result_name'   : '{}-<DS>'.format(name),
            'sample_name'   : name,
            'sample_type'   : 'Sample',
            }

        if sp_method is not None:
            sequence_vals['sp_method'] = sp_method

        self._submit_args = {
            'name'                  : name,
            'sequence_vals'         : sequence_vals,
            'result_path'           : result_path,
            'flow_rate'             : flow_rate,
            'wait_for_flow_ramp'    : wait_for_flow_ramp,
            'settle_time'           : settle_time,
            }

        logger.info(('HPLC %s starting to submit sample %s on active flow '
            'path %s'), self.name, name, self._active_flow_path)

        self._submitting_sample = True
        self._abort_submit.clear()
        self._monitor_submit_evt.set()

        return True

    def _monitor_submit(self):
        while not self._terminate_monitor_submit.is_set():
            self._monitor_submit_evt.wait()

            if (self._abort_submit.is_set()
                and self._terminate_monitor_submit.is_set()):
                break

            name = self._submit_args['name']
            sequence_vals = self._submit_args['sequence_vals']
            result_path = self._submit_args['result_path']
            flow_rate = self._submit_args['flow_rate']
            wait_for_flow_ramp = self._submit_args['wait_for_flow_ramp']
            settle_time = self._submit_args['settle_time']

            if wait_for_flow_ramp:
                while self.get_hplc_flow_rate(self._active_flow_path) != flow_rate:
                    if self._abort_submit.is_set():
                        break
                    time.sleep(0.1)

                start = time.time()

                while time.time() - start < settle_time:
                    if self._abort_submit.is_set():
                        break
                    time.sleep(0.1)

            if not self._abort_submit.is_set():
                self.submit_sequence(name, [sequence_vals], result_path, name)

            self._submitting_sample = False
            self._monitor_submit_evt.clear()

    def stop_equilibration(self, flow_path):
        """
        Stops the equilibration on the specified flow path

        Parameters
        ----------
        flow_path: int
            The flow path to stop the equilibration on. Either 1 or 2.
        """
        flow_path = int(flow_path)

        if flow_path == 1:
            if self._equil_flow1:
                self._remaining_equil1_vol = 0
                self._abort_equil1.set()

                if self._purging_flow1:
                    self.stop_purge(1)

        if flow_path == 2:
            if self._equil_flow2:
                self._remaining_equil2_vol = 0
                self._abort_equil2.set()

                if self._purging_flow2:
                    self.stop_purge(2)

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
        if self._switching_flow_path:
            self._abort_switch.set()
            logger.info('HPLC %s stoping switching of active flow path', self.name)

    def stop_submit_sample(self):
        """
        Stops current sample submission (will not abort the sample run if it's
        already submitted).
        """
        if self._submitting_sample:
            self._abort_submit.set()
            logger.info('HPLC %s aborted sample submission', self.name)

    def stop_all(self):
        """
        Stops all current actions, including purging, switching, submitting a
        sample. Pauses the run queue and aborts the current run.
        """
        self.stop_purge(1)
        self.stop_purge(2)
        self.stop_switch()
        self.stop_submit_sample()
        self.pause_run_queue()
        try:
            self.abort_current_run()
        except Exception:
            pass
        self.set_hplc_flow_rate(0, 1)
        self.set_hplc_flow_rate(0, 2)

    def stop_all_immediately(self):
        """
        Stops all current actions, including purging, switching, submitting a
        sample. Pauses the run queue and aborts the current run. Sets the flow
        acceleration to max to stop the pumps as quickly as possible.
        """
        flow_accel1 = self.get_hplc_flow_accel(1)
        flow_accel2 = self.get_hplc_flow_accel(2)

        self.stop_all()

        self.set_hplc_flow_accel(100, 1)
        self.set_hplc_flow_accel(100, 2)

        pump1_stopped = False
        pump2_stopped = False
        while not pump1_stopped or not pump2_stopped:
            if float(self.get_hplc_flow_rate(1)) == 0:
                pump1_stopped = True
            if float(self.get_hplc_flow_rate(2)) == 0:
                pump2_stopped = True

        self.set_hplc_flow_accel(flow_accel1, 1)
        self.set_hplc_flow_accel(flow_accel2, 2)

    def stop_pump1(self):
        """
        Stops pump 1.
        """
        self.stop_purge(1)
        self.set_hplc_flow_rate(0, 1)

    def stop_pump2(self):
        """
        Stops pump 2.
        """
        self.stop_purge(2)
        self.set_hplc_flow_rate(0, 2)

    def stop_pump1_immediately(self):
        """
        Stops pump 1 as quickly as possible by setting flow acceleration
        to max.
        """
        flow_accel1 = self.get_hplc_flow_accel(1)

        self.stop_pump1()

        self.set_hplc_flow_accel(100, 1)

        pump1_stopped = False
        while not pump1_stopped:
            if float(self.get_hplc_flow_rate(1)) == 0:
                pump1_stopped = True

        self.set_hplc_flow_accel(flow_accel1, 1)

    def stop_pump2_immediately(self):
        """
        Stops pump 2 as quickly as possible by setting flow acceleration
        to max.
        """
        flow_accel2 = self.get_hplc_flow_accel(2)

        self.stop_pump2()

        self.set_hplc_flow_accel(100, 2)

        pump1_stopped = False
        while not pump1_stopped:
            if float(self.get_hplc_flow_rate(2)) == 0:
                pump1_stopped = True

        self.set_hplc_flow_accel(flow_accel2, 2)

    def disconnect_all(self):
        """
        Use this method instead of disconnect to disconnect from both the
        valves and the HPLC.
        """
        self._buffer_monitor1.stop_monitor()
        self._buffer_monitor2.stop_monitor()

        for valve in self._valves.values():
            valve.disconnect()

        self._terminate_monitor_purge.set()
        self._monitor_purge_evt.set()
        self._monitor_purge_thread.join()

        self._abort_switch.set()
        self._terminate_monitor_switch.set()
        self._monitor_switch_evt.set()
        self._monitor_switch_thread.join()

        self._abort_submit.set()
        self._terminate_monitor_submit.set()
        self._monitor_submit_evt.set()
        self._monitor_submit_thread.join()

        self.disconnect()

known_hplcs = {
    'AgilentHPLC2Pumps'  : AgilentHPLC2Pumps,
    }

class HPLCCommThread(utils.CommManager):
    """
    Custom communication thread for HPLCs.
    """

    def __init__(self, name):
        """
        Initializes the custom thread. Important parameters here are the
        list of known commands ``_commands`` and known valves.
        """
        utils.CommManager.__init__(self, name)

        logger.info("Starting valve control thread: %s", self.name)

        self._commands = {
            'connect'                   : self._connect_device,
            'disconnect'                : self._disconnect_device,
            'get_valve_position'        : self._get_valve_position,
            'get_methods'               : self._get_methods,
            'get_sample_prep_methods'   : self._get_sample_prep_methods,
            'get_run_status'            : self._get_run_status,
            'get_fast_hplc_status'      : self._get_fast_hplc_status,
            'get_slow_hplc_status'      : self._get_slow_hplc_status,
            'get_valve_status'          : self._get_valve_status,
            'set_valve_position'        : self._set_valve_position,
            'purge_flow_path'           : self._purge_flow_path,
            'equil_flow_path'           : self._equil_flow_path,
            'set_active_flow_path'      : self._set_active_flow_path,
            'set_flow_rate'             : self._set_flow_rate,
            'set_flow_accel'            : self._set_flow_accel,
            'set_high_pressure_lim'     : self._set_high_pressure_lim,
            'set_pump_on'               : self._set_pump_on,
            'set_pump_standby'          : self._set_pump_standby,
            'set_autosampler_on'        : self._set_autosampler_on,
            'set_uv_on'                 : self._set_uv_on,
            'submit_sample'             : self._submit_sample,
            'stop_purge'                : self._stop_purge,
            'stop_equil'                : self._stop_equil,
            'stop_switch'               : self._stop_switch,
            'stop_sample_submission'    : self._stop_sample_submission,
            'stop_all'                  : self._stop_all,
            'stop_all_immediately'      : self._stop_all_immediately,
            'stop_pump1'                : self._stop_pump1,
            'stop_pump1_immediately'    : self._stop_pump1_immediately,
            'stop_pump2'                : self._stop_pump2,
            'stop_pump2_immediately'    : self._stop_pump2_immediately,
            'abort_current_run'         : self._abort_current_run,
            'pause_run_queue'           : self._pause_run_queue,
            'resume_run_queue'          : self._resume_run_queue,
            }

        self._connected_devices = OrderedDict()
        self._connected_coms = OrderedDict()

        self.known_devices = known_hplcs

    def _cleanup_devices(self):
        device_names = copy.copy(list(self._connected_devices.keys()))
        for name in device_names:
            self._disconnect_device(name)

    def _additional_new_comm(self, name):
        pass

    def _get_valve_position(self, name, vid, **kwargs):
        logger.debug("Getting %s valve %s position", name, vid)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_valve_position(vid, **kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("%s valve %s position: %s", name, vid, val)

    def _get_methods(self, name, **kwargs):
        logger.debug("Getting %s acquisition methods", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_methods(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("%s methods: %s", name, val)

    def _get_sample_prep_methods(self, name, **kwargs):
        logger.debug("Getting %s sample prep methods", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_sample_prep_methods(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("%s sample prep methods: %s", name, val)

    def _get_run_status(self, name, run_name, **kwargs):
        logger.debug("Getting %s run %s status", name, run_name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_run_status(run_name, **kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("%s run %s status: %s", name, run_name, val)

    def _get_fast_hplc_status(self, name, **kwargs):
        logger.debug("Getting %s fast status", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]

        a = time.time()
        instrument_status = {
            'status'            : device.get_instrument_status(),
            'connected'         : device.get_connected(),
            'errors'            : device.get_instrument_errors(),
            'run_queue_status'  : device.get_run_queue_status(),
            'run_queue'         : device.get_run_queue(),
            }

        pump_status = {
            'purging_pump1'     : device.get_purge_status(1),
            'equilibrate_pump1' : device.get_equilibration_status(1),
            'flow1'             : device.get_hplc_flow_rate(1),
            'pressure1'         : device.get_hplc_pressure(1),
            'all_buffer_info1'  : device.get_all_buffer_info(1),
            }

        if isinstance(device, AgilentHPLC2Pumps):
            pump_status['active_flow_path'] = device.get_active_flow_path()
            pump_status['purging_pump2']  = device.get_purge_status(2)
            pump_status['equilibrate_pump2']  = device.get_equilibration_status(2)
            pump_status['flow2'] = device.get_hplc_flow_rate(2)
            pump_status['pressure2'] = device.get_hplc_pressure(2)
            pump_status['switching_flow_path'] = device.get_flow_path_switch_status()
            pump_status['all_buffer_info2'] = device.get_all_buffer_info(2)

        autosampler_status = {
            'submitting_sample'         : device.get_submitting_sample_status(),
            'temperature'               : device.get_hplc_autosampler_temperature(),
            }

        val = {
            'instrument_status' : instrument_status,
            'pump_status'       : pump_status,
            'autosampler_status': autosampler_status,
        }

        self._return_value((name, cmd, val), comm_name)

        logger.debug("%s fast status: %s", name, val)

    def _get_slow_hplc_status(self, name, **kwargs):
        logger.debug("Getting %s slow status", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]

        a = time.time()

        pump_status = {
            'target_flow1'      : device.get_hplc_target_flow_rate(1),
            'flow_accel1'       : device.get_hplc_flow_accel(1, False),
            'power_status1'     : device.get_hplc_pump_power_status(1),
            'high_pressure_lim1': device.get_hplc_high_pressure_limit(1, False),
            }

        if isinstance(device, AgilentHPLC2Pumps):
            pump_status['target_flow2'] = device.get_hplc_target_flow_rate(2, False)
            pump_status['flow_accel2'] = device.get_hplc_flow_accel(2, False)
            pump_status['power_status2'] = device.get_hplc_pump_power_status(2)
            pump_status['high_pressure_lim2'] =device.get_hplc_high_pressure_limit(2,
                False)

        autosampler_status = {
            'thermostat_power_status'   : device.get_autosampler_thermostat_power_status(),
            }

        if len(device.get_uv_ids()) > 0:
            uv_status = {
                'uv_lamp_status'    : device.get_uv_lamp_power_status(),
                'vis_lamp_status'   : device.get_vis_lamp_power_status(),
                }

        else:
            uv_status = {}

        logger.debug(time.time()-a)

        val = {
            'pump_status'       : pump_status,
            'autosampler_status': autosampler_status,
            'uv_status'         : uv_status,
        }

        self._return_value((name, cmd, val), comm_name)

        logger.debug("%s slow status: %s", name, val)

    def _get_valve_status(self, name, **kwargs):
        logger.debug("Getting %s valve status", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]

        a = time.time()

        valve_status = {
            'buffer1'   : device.get_valve_position('buffer1'),
            }

        if isinstance(device, AgilentHPLC2Pumps):
            valve_status['buffer2'] = device.get_valve_position('buffer2')
            valve_status['purge1'] = device.get_valve_position('purge1')
            valve_status['purge2'] = device.get_valve_position('purge2')
            valve_status['selector'] = device.get_valve_position('selector')
            valve_status['outlet'] = device.get_valve_position('outlet')

        logger.debug(time.time()-a)

        self._return_value((name, cmd, valve_status), comm_name)

        logger.debug("%s valve status: %s", name, valve_status)

    def _set_valve_position(self, name, vid, val, **kwargs):
        logger.debug("Setting %s valve %s position", name, vid)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_valve_position(vid, val, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s valve %s position set: %s", name, vid, success)

    def _purge_flow_path(self, name, flow_path, purge_volume, **kwargs):
        logger.debug("Purging %s flow path %s", name, flow_path)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.purge_flow_path(flow_path, purge_volume, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s flow path %s purge started: %s", name, flow_path,
            success)

    def _equil_flow_path(self, name, flow_path, equil_volume, equil_rate,
        equil_accel, **kwargs):
        logger.debug("Equilibrating %s flow path %s", name, flow_path)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.equilibrate_flow_path(flow_path, equil_volume,
            equil_rate, equil_accel, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s flow path %s equilibration started: %s", name, flow_path,
            success)

    def _set_active_flow_path(self, name, flow_path, **kwargs):
        logger.debug("Setting %s active flow path %s", name, flow_path)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_active_flow_path(flow_path, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("%s active flow path %s started: %s", name, flow_path,
            success)

    def _set_flow_rate(self, name, val, flow_path, **kwargs):
        logger.debug("Setting %s flow path %s flow rate %s ", name, flow_path,
            val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_hplc_flow_rate(val, flow_path, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Set %s flow path %s flow rate %s: %s", name, flow_path,
            val, success)

    def _set_flow_accel(self, name, val, flow_path, **kwargs):
        logger.debug("Setting %s flow path %s flow accel %s ", name, flow_path,
            val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_hplc_flow_accel(val, flow_path, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Set %s flow path %s flow accel %s: %s", name, flow_path,
            val, success)

    def _set_high_pressure_lim(self, name, val, flow_path, **kwargs):
        logger.debug("Setting %s flow path %s high pressure limit %s ", name,
            flow_path, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_hplc_high_pressure_limit(val, flow_path, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Set %s flow path %s high pressure limit %s: %s", name,
            flow_path, val, success)

    def _set_buffer_info(self, name, position, volume, descrip, flow_path,
        **kwargs):
        logger.debug("Setting %s flow path %s buffer info %s: %s, %s", name,
            flow_path, position, volume, descrip)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_buffer_info(position, volume, descrip, flow_path,
            **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Set %s flow path %s buffer info %s", name, flow_path,
            position)

    def _set_pump_on(self, name, flow_path, **kwargs):
        logger.debug("Setting %s flow path %s pump on", name, flow_path)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_hplc_pump_on(flow_path, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Set %s flow path %s pump on: %s", name, flow_path,
            success)

    def _set_pump_standby(self, name, flow_path, **kwargs):
        logger.debug("Setting %s flow path %s pump standby", name, flow_path)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_hplc_pump_standby(flow_path, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Set %s flow path %s pump standby: %s", name, flow_path,
            success)

    def _set_autosampler_on(self, name, **kwargs):
        logger.debug("Setting %s autosampler on", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_autosampler_on(**kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Set %s autosampler on: %s", name, success)

    def _set_uv_on(self, name, **kwargs):
        logger.debug("Setting %s uv on", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_uv_on(**kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Set %s uv on: %s", name, success)

    def _submit_sample(self, name, sample_name, acq_method, sample_loc, inj_vol,
        flow_rate, flow_accel, total_elution_vol, high_pressure_lim, **kwargs):
        logger.debug("Submiting sample %s to %s", sample_name, name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.submit_hplc_sample(sample_name, acq_method,
            sample_loc, inj_vol, flow_rate, flow_accel, total_elution_vol,
            high_pressure_lim, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Submitted sample %s to %s", sample_name, name)

    def _stop_purge(self, name, flow_path, **kwargs):
        logger.debug("Stopping %s purge on flow path %s", name, flow_path)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.stop_purge(flow_path, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Stopped %s purge on flow path %s", name, flow_path)

    def _stop_equil(self, name, flow_path, **kwargs):
        logger.debug("Stopping %s equilibration on flow path %s", name, flow_path)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.stop_equilibration(flow_path, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Stopped %s equilibration on flow path %s", name, flow_path)

    def _stop_switch(self, name, **kwargs):
        logger.debug("Stopping %s active flow path switching", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.stop_switch(**kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Stopped %s active flow path switching", name)

    def _stop_sample_submission(self, name, **kwargs):
        logger.debug("Stopping %s sample submission", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.stop_submit_sample(**kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Stopped %s sample submission", name)

    def _stop_all(self, name, **kwargs):
        logger.debug("Stopping %s all actions", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.stop_all(**kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.info("Stopped %s all actions", name)

    def _stop_all_immediately(self, name, **kwargs):
        logger.debug("Stopping %s all actions immediately", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.stop_all_immediately(**kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.info("Stopped %s all actions immeidately", name)

    def _stop_pump1(self, name, **kwargs):
        logger.debug("Stopping %s pump1 actions", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.stop_pump1(**kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.info("Stopped %s pump1 actions", name)

    def _stop_pump1_immediately(self, name, **kwargs):
        logger.debug("Stopping %s pump1 actions immediately", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.stop_pump1_immediately(**kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.info("Stopped %s pump1 actions immeidately", name)

    def _stop_pump2(self, name, **kwargs):
        logger.debug("Stopping %s pump2 actions", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.stop_pump2(**kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.info("Stopped %s pump2 actions", name)

    def _stop_pump2_immediately(self, name, **kwargs):
        logger.debug("Stopping %s pump2 actions immediately", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.stop_pump2_immediately(**kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.info("Stopped %s pump2 actions immeidately", name)

    def _abort_current_run(self, name, **kwargs):
        logger.debug("Aborting %s current run", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.abort_current_run(**kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.info("Aborted %s current run", name)

    def _pause_run_queue(self, name, **kwargs):
        logger.debug("Pausing %s run queue", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.pause_run_queue(**kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.info("Paused %s run queue", name)

    def _resume_run_queue(self, name, **kwargs):
        logger.debug("Resuming %s run queue", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.resume_run_queue(**kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.info("Resumed %s run queue", name)

    def _reconnect(self, name, **kwargs):
        logger.debug("Reconnecting to %s", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.reconnect(**kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.info("Reconnected to %s", name)

    def _disconnect_device(self, name, **kwargs):
        # Override default because have to use disconnect_all
        logger.info("Disconnecting device %s", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices.pop(name, None)
        if device is not None:
            device.disconnect_all()

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s disconnected", name)


class HPLCPanel(utils.DevicePanel):
    """
    """
    def __init__(self, parent, panel_id, settings, *args, **kwargs):
        """
        HPLC control GUI panel, can be instance multiple times for multiple valves

        :param wx.Window parent: Parent class for the panel.

        :param int panel_id: wx ID for the panel.

        """
        self._device_type = settings['device_data']['args'][0]

        self._inst_connected = ''
        self._inst_status = ''
        self._inst_run_queue_status = ''
        self._inst_err_status = ''
        self._inst_errs = ''

        self._flow_path = ''
        self._flow_path_status = ''
        self._pump1_power = ''
        self._pump1_flow = ''
        self._pump1_flow_accel = ''
        self._pump1_pressure = ''
        self._pump1_pressure_lim = ''
        self._pump1_purge = ''
        self._pump1_purge_vol = ''
        self._pump1_eq = ''
        self._pump1_eq_vol = ''
        self._pump1_flow_target = ''
        self._pump2_power = ''
        self._pump2_flow = ''
        self._pump2_flow_accel = ''
        self._pump2_pressure = ''
        self._pump2_pressure_lim = ''
        self._pump2_purge = ''
        self._pump2_purge_vol = ''
        self._pump2_eq = ''
        self._pump2_eq_vol = ''
        self._pump2_flow_target = ''

        self._buffer1_valve = 0
        self._purge1_valve = 0
        self._buffer2_valve = 0
        self._purge2_valve = 0
        self._selector_valve = 0
        self._outlet_valve = 0

        self._sampler_thermostat_power = ''
        self._sampler_submitting = ''
        self._sampler_temp = ''

        super(HPLCPanel, self).__init__(parent, panel_id, settings,
            *args, **kwargs)


    def _create_layout(self):
        """Creates the layout for the panel."""

        inst_sizer = self._create_inst_ctrls()
        flow_sizer = self._create_flow_ctrls()
        sampler_sizer = self._create_sampler_ctrls()
        buffer_sizer = self._create_buffer_ctrls()

        sub_sizer1 = wx.BoxSizer(wx.HORIZONTAL)
        sub_sizer1.Add(sampler_sizer)
        sub_sizer1.Add(buffer_sizer, flag=wx.LEFT|wx.EXPAND,
            border=self._FromDIP(5))

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(inst_sizer, flag=wx.EXPAND, proportion=1)
        top_sizer.Add(flow_sizer, flag=wx.EXPAND)
        top_sizer.Add(sub_sizer1, flag=wx.EXPAND, proportion=1)

        self.Refresh()

        self.SetSizer(top_sizer)

    def _create_inst_ctrls(self):
        inst_box = wx.StaticBox(self, label='Instrument')

        self._inst_connected_ctrl = wx.StaticText(inst_box,
            size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
        self._inst_status_ctrl = wx.StaticText(inst_box,
            size=self._FromDIP((60,-1)), style=wx.ST_NO_AUTORESIZE)
        self._inst_run_queue_status_ctrl = wx.StaticText(inst_box,
            size=self._FromDIP((60,-1)), style=wx.ST_NO_AUTORESIZE)
        self._inst_err_status_ctrl = wx.StaticText(inst_box,
            size=self._FromDIP((60,-1)), style=wx.ST_NO_AUTORESIZE)
        self._inst_errs_ctrl = wx.TextCtrl(inst_box, size=self._FromDIP((60, 60)),
            style=wx.TE_MULTILINE|wx.TE_READONLY|wx.TE_BESTWRAP)


        inst_sizer = wx.GridBagSizer(vgap=self._FromDIP(5), hgap=self._FromDIP(5))
        inst_sizer.Add(wx.StaticText(inst_box, label='Connected:'),
            (0,0), flag=wx.ALIGN_CENTER_VERTICAL)
        inst_sizer.Add(self._inst_connected_ctrl, (0,1), flag=wx.ALIGN_CENTER_VERTICAL)
        inst_sizer.Add(wx.StaticText(inst_box, label='Status:'),
            (1,0), flag=wx.ALIGN_CENTER_VERTICAL)
        inst_sizer.Add(self._inst_status_ctrl, (1,1), flag=wx.ALIGN_CENTER_VERTICAL)
        inst_sizer.Add(wx.StaticText(inst_box, label='Run queue status:'),
            (2,0), flag=wx.ALIGN_TOP)
        inst_sizer.Add(self._inst_run_queue_status_ctrl, (2,1),
            flag=wx.ALIGN_TOP)
        inst_sizer.Add(wx.StaticText(inst_box, label='Error:'),
            (0,2), flag=wx.ALIGN_CENTER_VERTICAL)
        inst_sizer.Add(self._inst_err_status_ctrl, (0,3), flag=wx.ALIGN_CENTER_VERTICAL)
        inst_sizer.Add(self._inst_errs_ctrl, (1,2), flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND,
            span=(2,3))
        inst_sizer.AddGrowableCol(4)
        inst_sizer.AddGrowableRow(2)


        top_sizer = wx.StaticBoxSizer(inst_box, wx.VERTICAL)
        top_sizer.Add(inst_sizer, flag=wx.EXPAND|wx.ALL, proportion=1,
            border=self._FromDIP(5))

        return top_sizer

    def _create_flow_ctrls(self):
        flow_box = wx.StaticBox(self, label='Flow')

        pump1_box = wx.StaticBox(flow_box, label='Pump 1')
        self._pump1_power_ctrl = wx.StaticText(pump1_box,
            size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
        self._pump1_flow_ctrl = wx.StaticText(pump1_box,
            size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
        self._pump1_flow_accel_ctrl = wx.StaticText(pump1_box,
            size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
        self._pump1_pressure_ctrl = wx.StaticText(pump1_box,
            size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
        self._pump1_pressure_lim_ctrl = wx.StaticText(pump1_box,
            size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
        self._pump1_purge_ctrl = wx.StaticText(pump1_box,
            size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
        self._pump1_purge_vol_ctrl = wx.StaticText(pump1_box,
            size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
        self._pump1_eq_ctrl = wx.StaticText(pump1_box,
            size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
        self._pump1_eq_vol_ctrl = wx.StaticText(pump1_box,
            size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
        self._pump1_flow_target_ctrl = wx.StaticText(pump1_box,
            size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)

        pump1_status_sizer = wx.FlexGridSizer(vgap=self._FromDIP(5),
                hgap=self._FromDIP(5), cols=4)
        pump1_status_sizer.Add(wx.StaticText(pump1_box, label='Power:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(self._pump1_power_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.AddSpacer(1)
        pump1_status_sizer.AddSpacer(1)
        pump1_status_sizer.Add(wx.StaticText(pump1_box, label='Purging:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(self._pump1_purge_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(wx.StaticText(pump1_box, label='Purge vol.:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(self._pump1_purge_vol_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(wx.StaticText(pump1_box, label='Equil.:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(self._pump1_eq_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(wx.StaticText(pump1_box, label='Eq. vol.:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(self._pump1_eq_vol_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(wx.StaticText(pump1_box, label='Flow (ml/min):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(self._pump1_flow_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(wx.StaticText(pump1_box, label='Flow setpoint:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(self._pump1_flow_target_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(wx.StaticText(pump1_box, label='Flow accel.:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(self._pump1_flow_accel_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.AddSpacer(1)
        pump1_status_sizer.AddSpacer(1)
        pump1_status_sizer.Add(wx.StaticText(pump1_box, label='Pressure (bar):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(self._pump1_pressure_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(wx.StaticText(pump1_box, label='Pressure lim.:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(self._pump1_pressure_lim_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)

        self._set_pump1_flow_rate_ctrl = wx.TextCtrl(pump1_box,
            size=self._FromDIP((60,-1)), validator=utils.CharValidator('float'))
        self._set_pump1_flow_accel_ctrl = wx.TextCtrl(pump1_box,
            size=self._FromDIP((60,-1)), validator=utils.CharValidator('float'))
        self._set_pump1_pressure_lim_ctrl = wx.TextCtrl(pump1_box,
            size=self._FromDIP((60,-1)), validator=utils.CharValidator('float'))

        self._set_pump1_flow_rate_btn = wx.Button(pump1_box, label='Set')
        self._set_pump1_flow_accel_btn = wx.Button(pump1_box, label='Set')
        self._set_pump1_pressure_lim_btn = wx.Button(pump1_box, label='Set')
        self._pump1_stop_btn = wx.Button(pump1_box, label='Stop Flow')
        self._pump1_stop_now_btn = wx.Button(pump1_box, label='Stop Flow NOW')
        self._pump1_purge_btn = wx.Button(pump1_box, label='Purge')
        self._pump1_stop_purge_btn = wx.Button(pump1_box, label='Stop Purge')
        self._pump1_eq_btn = wx.Button(pump1_box, label='Equilibrate')
        self._pump1_stop_eq_btn = wx.Button(pump1_box, label='Stop Equil.')
        self._pump1_on_btn = wx.Button(pump1_box, label='Pump On')
        self._pump1_standby_btn = wx.Button(pump1_box, label='Pump Standby')

        self._set_pump1_flow_rate_btn.Bind(wx.EVT_BUTTON, self._on_set_flow)
        self._set_pump1_flow_accel_btn.Bind(wx.EVT_BUTTON, self._on_set_flow_accel)
        self._set_pump1_pressure_lim_btn.Bind(wx.EVT_BUTTON, self._on_set_pressure_lim)
        self._pump1_stop_btn.Bind(wx.EVT_BUTTON, self._on_stop_flow)
        self._pump1_stop_now_btn.Bind(wx.EVT_BUTTON, self._on_stop_flow_now)
        self._pump1_purge_btn.Bind(wx.EVT_BUTTON, self._on_purge)
        self._pump1_stop_purge_btn.Bind(wx.EVT_BUTTON, self._on_stop_purge)
        self._pump1_eq_btn.Bind(wx.EVT_BUTTON, self._on_eq)
        self._pump1_stop_eq_btn.Bind(wx.EVT_BUTTON, self._on_stop_eq)
        self._pump1_on_btn.Bind(wx.EVT_BUTTON, self._on_pump_on)
        self._pump1_standby_btn.Bind(wx.EVT_BUTTON, self._on_pump_standby)


        pump1_ctrl_sizer1 = wx.FlexGridSizer(vgap=self._FromDIP(5),
            hgap=self._FromDIP(5), cols=3)
        pump1_ctrl_sizer1.Add(wx.StaticText(pump1_box, label='Set flow rate:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_ctrl_sizer1.Add(self._set_pump1_flow_rate_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_ctrl_sizer1.Add(self._set_pump1_flow_rate_btn,
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_ctrl_sizer1.Add(wx.StaticText(pump1_box, label='Set flow accel.:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_ctrl_sizer1.Add(self._set_pump1_flow_accel_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_ctrl_sizer1.Add(self._set_pump1_flow_accel_btn,
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_ctrl_sizer1.Add(wx.StaticText(pump1_box, label='Set pressure lim.:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_ctrl_sizer1.Add(self._set_pump1_pressure_lim_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_ctrl_sizer1.Add(self._set_pump1_pressure_lim_btn,
            flag=wx.ALIGN_CENTER_VERTICAL)

        pump1_btn_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        pump1_btn_sizer.Add(self._pump1_eq_btn)
        pump1_btn_sizer.Add(self._pump1_stop_eq_btn)
        pump1_btn_sizer.Add(self._pump1_purge_btn)
        pump1_btn_sizer.Add(self._pump1_stop_purge_btn)
        pump1_btn_sizer.Add(self._pump1_stop_btn)
        pump1_btn_sizer.Add(self._pump1_stop_now_btn)
        pump1_btn_sizer.Add(self._pump1_on_btn)
        pump1_btn_sizer.Add(self._pump1_standby_btn)


        pump1_sizer = wx.StaticBoxSizer(pump1_box, wx.VERTICAL)
        pump1_sizer.Add(pump1_status_sizer, flag=wx.ALL, border=self._FromDIP(5))
        pump1_sizer.Add(pump1_ctrl_sizer1, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))
        pump1_sizer.Add(pump1_btn_sizer, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))


        if self._device_type == 'AgilentHPLC2Pumps':
            flow_path_box = wx.StaticBox(flow_box, label='Flow Path')
            self._flow_path_ctrl = wx.StaticText(flow_path_box,
                size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
            self._flow_path_status_ctrl = wx.StaticText(flow_path_box,
                size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)

            self._set_path1_btn = wx.Button(flow_path_box, label='Set Path 1')
            self._set_path2_btn = wx.Button(flow_path_box, label='Set Path 2')
            self._set_path1_btn.Bind(wx.EVT_BUTTON, self._on_set_flow_path)
            self._set_path2_btn.Bind(wx.EVT_BUTTON, self._on_set_flow_path)

            self._stop_set_path_btn = wx.Button(flow_path_box,
                label='Stop switching flow path')
            self._stop_set_path_btn.Bind(wx.EVT_BUTTON, self._on_stop_switch)

            fp_status_sizer = wx.FlexGridSizer(vgap=self._FromDIP(5),
                hgap=self._FromDIP(5), cols=2)
            fp_status_sizer.Add(wx.StaticText(flow_path_box,
                label='Active flow path:'), flag=wx.ALIGN_CENTER_VERTICAL)
            fp_status_sizer.Add(self._flow_path_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
            fp_status_sizer.Add(wx.StaticText(flow_path_box,
                label='Switching flow path:'), flag=wx.ALIGN_CENTER_VERTICAL)
            fp_status_sizer.Add(self._flow_path_status_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)

            fp_button_sizer = wx.BoxSizer(wx.HORIZONTAL)
            fp_button_sizer.Add(self._set_path1_btn, border=self._FromDIP(5),
                flag=wx.RIGHT|wx.ALIGN_CENTER_VERTICAL)
            fp_button_sizer.Add(self._set_path2_btn,
                flag=wx.ALIGN_CENTER_VERTICAL)

            fp_button_sizer2 = wx.BoxSizer(wx.HORIZONTAL)
            fp_button_sizer2.Add(self._stop_set_path_btn,
                flag=wx.ALIGN_CENTER_VERTICAL)
            fp_button_sizer2.AddStretchSpacer(1)

            fp_sizer = wx.StaticBoxSizer(flow_path_box, wx.VERTICAL)
            fp_sizer.Add(fp_status_sizer, flag=wx.ALL, border=self._FromDIP(5))
            fp_sizer.Add(fp_button_sizer, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
                border=self._FromDIP(5))
            fp_sizer.Add(fp_button_sizer2, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM|wx.EXPAND,
                border=self._FromDIP(5))


            self._stop_all_btn = wx.Button(flow_box, label='Stop All')
            self._stop_all_now_btn = wx.Button(flow_box, label='Stop All NOW')
            self._stop_all_btn.Bind(wx.EVT_BUTTON, self._on_stop_all)
            self._stop_all_now_btn.Bind(wx.EVT_BUTTON, self._on_stop_all_now)

            stop_all_sizer = wx.BoxSizer(wx.HORIZONTAL)
            stop_all_sizer.Add(self._stop_all_btn, border=self._FromDIP(5),
                flag=wx.RIGHT|wx.ALIGN_CENTER_VERTICAL)
            stop_all_sizer.Add(self._stop_all_now_btn,
                flag=wx.ALIGN_CENTER_VERTICAL)

            mid_sizer = wx.BoxSizer(wx.VERTICAL)
            mid_sizer.Add(stop_all_sizer, flag=wx.ALIGN_CENTER_HORIZONTAL)
            mid_sizer.Add(fp_sizer, flag=wx.TOP|wx.EXPAND, border=self._FromDIP(5))



            pump2_box = wx.StaticBox(flow_box, label='Pump 2')
            self._pump2_power_ctrl = wx.StaticText(pump2_box,
                size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
            self._pump2_flow_ctrl = wx.StaticText(pump2_box,
                size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
            self._pump2_flow_accel_ctrl = wx.StaticText(pump2_box,
                size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
            self._pump2_pressure_ctrl = wx.StaticText(pump2_box,
                size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
            self._pump2_pressure_lim_ctrl = wx.StaticText(pump2_box,
                size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
            self._pump2_purge_ctrl = wx.StaticText(pump2_box,
                size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
            self._pump2_purge_vol_ctrl = wx.StaticText(pump2_box,
                size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
            self._pump2_eq_ctrl = wx.StaticText(pump2_box,
                size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
            self._pump2_eq_vol_ctrl = wx.StaticText(pump2_box,
                size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
            self._pump2_flow_target_ctrl = wx.StaticText(pump2_box,
                size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)

            pump2_status_sizer = wx.FlexGridSizer(vgap=self._FromDIP(5),
                    hgap=self._FromDIP(5), cols=4)
            pump2_status_sizer.Add(wx.StaticText(pump2_box, label='Power:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(self._pump2_power_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.AddSpacer(1)
            pump2_status_sizer.AddSpacer(1)
            pump2_status_sizer.Add(wx.StaticText(pump2_box, label='Purging:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(self._pump2_purge_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(wx.StaticText(pump2_box, label='Purge vol.:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(self._pump2_purge_vol_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(wx.StaticText(pump2_box, label='Equil.:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(self._pump2_eq_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(wx.StaticText(pump2_box, label='Eq. vol.:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(self._pump2_eq_vol_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(wx.StaticText(pump2_box, label='Flow (ml/min):'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(self._pump2_flow_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(wx.StaticText(pump2_box, label='Flow setpoint:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(self._pump2_flow_target_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(wx.StaticText(pump2_box, label='Flow accel.:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(self._pump2_flow_accel_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.AddSpacer(1)
            pump2_status_sizer.AddSpacer(1)
            pump2_status_sizer.Add(wx.StaticText(pump2_box, label='Pressure (bar):'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(self._pump2_pressure_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(wx.StaticText(pump2_box, label='Pressure lim.:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(self._pump2_pressure_lim_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)

            self._set_pump2_flow_rate_ctrl = wx.TextCtrl(pump2_box,
                size=self._FromDIP((60,-1)), validator=utils.CharValidator('float'))
            self._set_pump2_flow_accel_ctrl = wx.TextCtrl(pump2_box,
                size=self._FromDIP((60,-1)), validator=utils.CharValidator('float'))
            self._set_pump2_pressure_lim_ctrl = wx.TextCtrl(pump2_box,
                size=self._FromDIP((60,-1)), validator=utils.CharValidator('float'))

            self._set_pump2_flow_rate_btn = wx.Button(pump2_box, label='Set')
            self._set_pump2_flow_accel_btn = wx.Button(pump2_box, label='Set')
            self._set_pump2_pressure_lim_btn = wx.Button(pump2_box, label='Set')
            self._pump2_stop_btn = wx.Button(pump2_box, label='Stop Flow')
            self._pump2_stop_now_btn = wx.Button(pump2_box, label='Stop Flow NOW')
            self._pump2_purge_btn = wx.Button(pump2_box, label='Purge')
            self._pump2_stop_purge_btn = wx.Button(pump2_box, label='Stop Purge')
            self._pump2_eq_btn = wx.Button(pump2_box, label='Equilibrate')
            self._pump2_stop_eq_btn = wx.Button(pump2_box, label='Stop Equil.')
            self._pump2_on_btn = wx.Button(pump2_box, label='Pump On')
            self._pump2_standby_btn = wx.Button(pump2_box, label='Pump Standby')

            self._set_pump2_flow_rate_btn.Bind(wx.EVT_BUTTON, self._on_set_flow)
            self._set_pump2_flow_accel_btn.Bind(wx.EVT_BUTTON, self._on_set_flow_accel)
            self._set_pump2_pressure_lim_btn.Bind(wx.EVT_BUTTON, self._on_set_pressure_lim)
            self._pump2_stop_btn.Bind(wx.EVT_BUTTON, self._on_stop_flow)
            self._pump2_stop_now_btn.Bind(wx.EVT_BUTTON, self._on_stop_flow_now)
            self._pump2_purge_btn.Bind(wx.EVT_BUTTON, self._on_purge)
            self._pump2_stop_purge_btn.Bind(wx.EVT_BUTTON, self._on_stop_purge)
            self._pump2_eq_btn.Bind(wx.EVT_BUTTON, self._on_eq)
            self._pump2_stop_eq_btn.Bind(wx.EVT_BUTTON, self._on_stop_eq)
            self._pump2_on_btn.Bind(wx.EVT_BUTTON, self._on_pump_on)
            self._pump2_standby_btn.Bind(wx.EVT_BUTTON, self._on_pump_standby)


            pump2_ctrl_sizer1 = wx.FlexGridSizer(vgap=self._FromDIP(5),
                hgap=self._FromDIP(5), cols=3)
            pump2_ctrl_sizer1.Add(wx.StaticText(pump2_box, label='Set flow rate:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_ctrl_sizer1.Add(self._set_pump2_flow_rate_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_ctrl_sizer1.Add(self._set_pump2_flow_rate_btn,
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_ctrl_sizer1.Add(wx.StaticText(pump2_box, label='Set flow accel.:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_ctrl_sizer1.Add(self._set_pump2_flow_accel_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_ctrl_sizer1.Add(self._set_pump2_flow_accel_btn,
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_ctrl_sizer1.Add(wx.StaticText(pump2_box, label='Set pressure lim.:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_ctrl_sizer1.Add(self._set_pump2_pressure_lim_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_ctrl_sizer1.Add(self._set_pump2_pressure_lim_btn,
                flag=wx.ALIGN_CENTER_VERTICAL)

            pump2_btn_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5),
                hgap=self._FromDIP(5))
            pump2_btn_sizer.Add(self._pump2_eq_btn)
            pump2_btn_sizer.Add(self._pump2_stop_eq_btn)
            pump2_btn_sizer.Add(self._pump2_purge_btn)
            pump2_btn_sizer.Add(self._pump2_stop_purge_btn)
            pump2_btn_sizer.Add(self._pump2_stop_btn)
            pump2_btn_sizer.Add(self._pump2_stop_now_btn)
            pump2_btn_sizer.Add(self._pump2_on_btn)
            pump2_btn_sizer.Add(self._pump2_standby_btn)


            pump2_sizer = wx.StaticBoxSizer(pump2_box, wx.VERTICAL)
            pump2_sizer.Add(pump2_status_sizer, flag=wx.ALL, border=self._FromDIP(5))
            pump2_sizer.Add(pump2_ctrl_sizer1, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
                border=self._FromDIP(5))
            pump2_sizer.Add(pump2_btn_sizer, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
                border=self._FromDIP(5))


        valve_sizer = self._create_valve_ctrls(flow_box)

        mid_sizer.Add(valve_sizer, flag=wx.TOP, border=self._FromDIP(5))

        flow_sizer = wx.BoxSizer(wx.HORIZONTAL)

        if self._device_type == 'AgilentHPLC2Pumps':
            flow_sizer.Add(mid_sizer, flag=wx.ALL, border=self._FromDIP(5))

        flow_sizer.Add(pump1_sizer, flag=wx.TOP|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))

        if self._device_type == 'AgilentHPLC2Pumps':
            flow_sizer.Add(pump2_sizer, flag=wx.TOP|wx.RIGHT|wx.BOTTOM,
                border=self._FromDIP(5))

        top_sizer = wx.StaticBoxSizer(flow_box, wx.VERTICAL)
        top_sizer.Add(flow_sizer, flag=wx.EXPAND, proportion=1)

        return top_sizer

    def _create_valve_ctrls(self, parent):
        valve_box = wx.StaticBox(parent, label='Valves')

        self._buffer1_valve_ctrl = utils.IntSpinCtrl(valve_box, my_min=1)
        self._purge1_valve_ctrl = utils.IntSpinCtrl(valve_box, my_min=1)

        self._buffer1_valve_ctrl.Bind(utils.EVT_MY_SPIN,
            self._on_set_valve_position)
        self._purge1_valve_ctrl.Bind(utils.EVT_MY_SPIN,
            self._on_set_valve_position)

        if self._device_type == 'AgilentHPLC2Pumps':
            self._buffer2_valve_ctrl = utils.IntSpinCtrl(valve_box, my_min=1)
            self._purge2_valve_ctrl = utils.IntSpinCtrl(valve_box, my_min=1)
            self._selector_valve_ctrl = utils.IntSpinCtrl(valve_box, my_min=1)
            self._outlet_valve_ctrl = utils.IntSpinCtrl(valve_box, my_min=1)

            self._buffer2_valve_ctrl.Bind(utils.EVT_MY_SPIN,
                self._on_set_valve_position)
            self._purge2_valve_ctrl.Bind(utils.EVT_MY_SPIN,
                self._on_set_valve_position)
            self._selector_valve_ctrl.Bind(utils.EVT_MY_SPIN,
                self._on_set_valve_position)
            self._outlet_valve_ctrl.Bind(utils.EVT_MY_SPIN,
                self._on_set_valve_position)

        valve_sizer = wx.FlexGridSizer(cols=4, vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        valve_sizer.Add(wx.StaticText(valve_box, label='Buffer 1:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        valve_sizer.Add(self._buffer1_valve_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        valve_sizer.Add(wx.StaticText(valve_box, label='Purge 1:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        valve_sizer.Add(self._purge1_valve_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)

        if self._device_type == 'AgilentHPLC2Pumps':
            valve_sizer.Add(wx.StaticText(valve_box, label='Buffer 2:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            valve_sizer.Add(self._buffer2_valve_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)
            valve_sizer.Add(wx.StaticText(valve_box, label='Purge 2:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            valve_sizer.Add(self._purge2_valve_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)
            valve_sizer.Add(wx.StaticText(valve_box, label='Selector:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            valve_sizer.Add(self._selector_valve_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)
            valve_sizer.Add(wx.StaticText(valve_box, label='Outlet:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            valve_sizer.Add(self._outlet_valve_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)

        top_sizer = wx.StaticBoxSizer(valve_box, wx.VERTICAL)
        top_sizer.Add(valve_sizer, flag=wx.ALL|wx.EXPAND, proportion=1,
            border=self._FromDIP(5))

        return top_sizer

    def _create_sampler_ctrls(self):
        sampler_box = wx.StaticBox(self, label='Autosampler')

        self._sampler_thermostat_power_ctrl = wx.StaticText(sampler_box,
            size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
        self._sampler_submitting_ctrl = wx.StaticText(sampler_box,
            size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
        self._sampler_temp_ctrl = wx.StaticText(sampler_box,
            size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)

        sampler_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        sampler_sizer.Add(wx.StaticText(sampler_box, label='Submitting sample:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        sampler_sizer.Add(self._sampler_submitting_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        sampler_sizer.Add(wx.StaticText(sampler_box, label='Thermostat:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        sampler_sizer.Add(self._sampler_thermostat_power_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        sampler_sizer.Add(wx.StaticText(sampler_box, label='Temperature (C):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        sampler_sizer.Add(self._sampler_temp_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)

        self._stop_sample_submission_btn = wx.Button(sampler_box,
            label='Stop Sample Submission')
        self._sampler_power_on_btn = wx.Button(sampler_box,
            label='Autosampler On')

        self._stop_sample_submission_btn.Bind(wx.EVT_BUTTON,
            self._on_stop_submission)
        self._sampler_power_on_btn.Bind(wx.EVT_BUTTON,
            self._on_sampler_power_on)

        sampler_btn_sizer = wx.BoxSizer(wx.VERTICAL)
        sampler_btn_sizer.Add(self._stop_sample_submission_btn)
        sampler_btn_sizer.Add(self._sampler_power_on_btn, flag=wx.TOP,
            border=self._FromDIP(5))

        top_sizer = wx.StaticBoxSizer(sampler_box, wx.VERTICAL)
        top_sizer.Add(sampler_sizer, flag=wx.ALL|wx.EXPAND,
            border=self._FromDIP(5))
        top_sizer.Add(sampler_btn_sizer, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))

        return top_sizer

    def _create_buffer_ctrls(self):
        buffer_box = wx.StaticBox(self, label='Buffers')

        buffer1_box = wx.StaticBox(self, label='Buffer 1')

        self._buffer1_list = wx.ListCtrl(buffer1_box,
            size=self._FromDIP((-1, 100)),style=wx.LC_REPORT|wx.BORDER_SUNKEN)

        self._buffer1_list.InsertColumn(0, 'Port')
        self._buffer1_list.InsertColumn(1, 'Vol. (L)')
        self._buffer1_list.InsertColumn(2, 'Buffer')

        buffer1_sizer = wx.StaticBoxSizer(buffer1_box, wx.VERTICAL)
        buffer1_sizer.Add(self._buffer1_list, flag=wx.EXPAND|wx.ALL,
            proportion=1, border=self._FromDIP(5))

        top_sizer = wx.StaticBoxSizer(buffer_box, wx.HORIZONTAL)
        top_sizer.Add(buffer1_sizer, flag=wx.EXPAND|wx.ALL, proportion=1,
            border=self._FromDIP(5))

        return top_sizer

    def _init_device(self, settings):
        """
        Initializes the valve.
        """
        device_data = settings['device_data']
        args = device_data['args']
        kwargs = device_data['kwargs']

        valve_max = kwargs['purge1_valve_args']['kwargs']['positions']
        self._purge1_valve_ctrl.SetMax(valve_max)

        valve_max = kwargs['buffer1_valve_args']['kwargs']['positions']
        self._buffer1_valve_ctrl.SetMax(valve_max)

        if self._device_type == 'AgilentHPLC2Pumps':
            valve_max = kwargs['purge2_valve_args']['kwargs']['positions']
            self._purge2_valve_ctrl.SetMax(valve_max)

            valve_max = kwargs['buffer2_valve_args']['kwargs']['positions']
            self._buffer2_valve_ctrl.SetMax(valve_max)

            valve_max = kwargs['selector_valve_args']['kwargs']['positions']
            self._selector_valve_ctrl.SetMax(valve_max)

            valve_max = kwargs['outlet_valve_args']['kwargs']['positions']
            self._outlet_valve_ctrl.SetMax(valve_max)

        args.insert(0, self.name)

        connect_cmd = ['connect', args, kwargs]

        connected = self._send_cmd(connect_cmd, True)

        if connected:
            get_fast_hplc_status_cmd = ['get_fast_hplc_status', [self.name,], {}]
            self._update_status_cmd(get_fast_hplc_status_cmd, 1)

            get_slow_hplc_status_cmd = ['get_slow_hplc_status', [self.name,], {}]
            self._update_status_cmd(get_slow_hplc_status_cmd, 30)

            get_valve_status_cmd = ['get_valve_status', [self.name,], {}]
            self._update_status_cmd(get_valve_status_cmd, 15)

        logger.info('Initialized HPLC %s on startup', self.name)

    def _on_error_collapse(self, evt):
        self.Layout()
        self.Refresh()
        self.SendSizeEvent()

    def _on_set_flow_path(self, evt):
        evt_obj = evt.GetEventObject()

        if self._set_path1_btn == evt_obj:
            flow_path = 1
        elif self._set_path2_btn == evt_obj:
            flow_path = 2

        # Get these values either from settings or with a dialog (or both)
        stop_flow1 = False
        stop_flow2 = False
        restore_flow_after_switch = True
        purge_active = True
        purge_volume = 0.1
        purge_rate = 0.1
        purge_accel = 1
        switch_with_sample = False

        kwargs = {
            'stop_flow1'    : stop_flow1,
            'stop_flow2'    : stop_flow2,
            'restore_flow_after_switch' : restore_flow_after_switch,
            'purge_active'  : purge_active,
            'purge_volume'  : purge_volume,
            'purge_rate'    : purge_rate,
            'purge_accel'   : purge_accel,
            'switch_with_sample'    : switch_with_sample,
            }

        cmd = ['set_active_flow_path', [self.name, flow_path], kwargs]
        self._send_cmd(cmd, False)

    def _on_stop_all(self, evt):
        cmd = ['stop_all', [self.name,], {}]
        self._send_cmd(cmd, False)

    def _on_stop_all_now(self, evt):
        cmd = ['stop_all_immediately', [self.name,], {}]
        self._send_cmd(cmd, False)

    def _on_stop_switch(self, evt):
        cmd = ['stop_switch', [self.name,], {}]
        self._send_cmd(cmd, False)

    def _on_set_flow(self, evt):
        evt_obj = evt.GetEventObject()

        if self._set_pump1_flow_rate_btn == evt_obj:
            flow_path = 1
            val = self._set_pump1_flow_rate_ctrl.GetValue()

        elif self._set_pump2_flow_rate_btn == evt_obj:
            flow_path = 2
            val = self._set_pump2_flow_rate_ctrl.GetValue()

        try:
            val = float(val)
        except ValueError:
            val = None

        if val is not None:
            cmd = ['set_flow_rate', [self.name, val, flow_path], {}]
            self._send_cmd(cmd, False)

            if flow_path == 1:
                if str(val) != self._pump1_flow_target:
                        wx.CallAfter(self._pump1_flow_target_ctrl.SetLabel, str(val))
                        self._pump1_flow_target = str(val)

            elif flow_path == 1:
                if str(val) != self._pump2_flow_target:
                        wx.CallAfter(self._pump2_flow_target_ctrl.SetLabel, str(val))
                        self._pump2_flow_target = str(val)

    def _on_set_flow_accel(self, evt):
        evt_obj = evt.GetEventObject()

        if self._set_pump1_flow_accel_btn == evt_obj:
            flow_path = 1
            val = self._set_pump1_flow_accel_ctrl.GetValue()
        elif self._set_pump2_flow_accel_btn == evt_obj:
            flow_path = 2
            val = self._set_pump2_flow_accel_ctrl.GetValue()

        try:
            val = float(val)
        except ValueError:
            val = None

        if val is not None:
            cmd = ['set_flow_accel', [self.name, val, flow_path], {}]
            self._send_cmd(cmd, False)

            if flow_path == 1:
                if str(val) != self._pump1_flow_accel:
                    wx.CallAfter(self._pump1_flow_accel_ctrl.SetLabel, str(val))
                    self._pump1_flow_accel = str(val)

            elif flow_path == 2:
                if str(val) != self._pump2_flow_accel:
                    wx.CallAfter(self._pump2_flow_accel_ctrl.SetLabel, str(val))
                    self._pump2_flow_accel = str(val)

    def _on_set_pressure_lim(self, evt):
        evt_obj = evt.GetEventObject()

        if self._set_pump1_pressure_lim_btn == evt_obj:
            flow_path = 1
            val = self._set_pump1_pressure_lim_ctrl.GetValue()
        elif self._set_pump2_pressure_lim_btn == evt_obj:
            flow_path = 2
            val = self._set_pump2_pressure_lim_ctrl.GetValue()

        try:
            val = float(val)
        except ValueError:
            val = None

        if val is not None:
            cmd = ['set_high_pressure_lim', [self.name, val, flow_path], {}]
            self._send_cmd(cmd, False)

            if flow_path == 1:
                if str(val) != self._pump1_pressure_lim:
                    wx.CallAfter(self._pump1_pressure_lim_ctrl.SetLabel, str(val))
                    self._pump1_pressure_lim = str(val)

            elif flow_path == 2:
                if str(val) != self._pump2_pressure_lim:
                    wx.CallAfter(self._pump2_pressure_lim_ctrl.SetLabel, str(val))
                    self._pump2_pressure_lim = str(val)

    def _on_stop_flow(self, evt):
        evt_obj = evt.GetEventObject()

        if self._pump1_stop_btn == evt_obj:
            flow_path = 1
        elif self._pump2_stop_btn == evt_obj:
            flow_path = 2

        cmd = ['stop_pump{}'.format(flow_path), [self.name,], {}]
        self._send_cmd(cmd, False)

        if flow_path == 1:
            if '0.0' != self._pump1_flow_target:
                    wx.CallAfter(self._pump1_flow_target_ctrl.SetLabel, '0.0')
                    self._pump1_flow_target = '0.0'

        elif flow_path == 1:
            if '0.0' != self._pump2_flow_target:
                    wx.CallAfter(self._pump2_flow_target_ctrl.SetLabel, '0.0')
                    self._pump2_flow_target = '0.0'

    def _on_stop_flow_now(self, evt):
        evt_obj = evt.GetEventObject()

        if self._pump1_stop_now_btn == evt_obj:
            flow_path = 1
        elif self._pump2_stop_now_btn == evt_obj:
            flow_path = 2

        cmd = ['stop_pump{}_immediately'.format(flow_path), [self.name,], {}]
        self._send_cmd(cmd, False)

        if flow_path == 1:
            if '0.0' != self._pump1_flow_target:
                    wx.CallAfter(self._pump1_flow_target_ctrl.SetLabel, '0.0')
                    self._pump1_flow_target = '0.0'

        elif flow_path == 1:
            if '0.0' != self._pump2_flow_target:
                    wx.CallAfter(self._pump2_flow_target_ctrl.SetLabel, '0.0')
                    self._pump2_flow_target = '0.0'

    def _on_purge(self, evt):
        evt_obj = evt.GetEventObject()

        if self._pump1_purge_btn == evt_obj:
            flow_path = 1
        elif self._pump2_purge_btn == evt_obj:
            flow_path = 2

        # Get these values either from settings or with a dialog (or both)
        purge_volume = 0.1
        purge_rate = 0.1
        purge_accel = 1
        restore_flow_after_purge = True
        purge_with_sample = False
        stop_before_purge = True
        stop_after_purge = True

        kwargs = {
            'purge_rate'    : purge_rate,
            'purge_accel'   : purge_accel,
            'restore_flow_after_purge'  : restore_flow_after_purge,
            'purge_with_sample' : purge_with_sample,
            'stop_before_purge' : stop_before_purge,
            'stop_after_purge'  : stop_after_purge,
        }

        cmd = ['purge_flow_path', [self.name, flow_path, purge_volume], kwargs]
        self._send_cmd(cmd, False)

    def _on_stop_purge(self, evt):
        evt_obj = evt.GetEventObject()

        if self._pump1_stop_purge_btn == evt_obj:
            flow_path = 1
        elif self._pump2_stop_purge_btn == evt_obj:
            flow_path = 2

        cmd = ['stop_purge', [self.name, flow_path,], {}]
        self._send_cmd(cmd, False)

    def _on_eq(self, evt):
        evt_obj = evt.GetEventObject()

        if self._pump1_eq_btn == evt_obj:
            flow_path = 1
        elif self._pump2_eq_btn == evt_obj:
            flow_path = 2

        # Get this from settings or through a dialog
        equil_volume = 0.1
        equil_rate = 0.1
        equil_accel = 0.1

        kwargs = {
            'purge' : True,
            'purge_volume'  : 0.1,
            'purge_rate'    : 0.1,
            'purge_accel'   : 1,
            'equil_with_sample' : False,
            'stop_after_equil'  : True,
            }

        cmd = ['equil_flow_path', [self.name, flow_path, equil_volume,
            equil_rate, equil_accel], kwargs]
        self._send_cmd(cmd, False)

    def _on_stop_eq(self, evt):
        evt_obj = evt.GetEventObject()

        if self._pump1_stop_eq_btn == evt_obj:
            flow_path = 1
        elif self._pump2_stop_eq_btn == evt_obj:
            flow_path = 2

        cmd = ['stop_equil', [self.name, flow_path,], {}]
        self._send_cmd(cmd, False)

    def _on_pump_on(self, evt):
        evt_obj = evt.GetEventObject()

        if self._pump1_on_btn == evt_obj:
            flow_path = 1
        elif self._pump2_on_btn == evt_obj:
            flow_path = 2

        cmd = ['set_pump_on', [self.name, flow_path], {}]
        self._send_cmd(cmd, False)

    def _on_pump_standby(self, evt):
        evt_obj = evt.GetEventObject()

        if self._pump1_standby_btn == evt_obj:
            flow_path = 1
        elif self._pump2_standby_btn == evt_obj:
            flow_path = 2

        cmd = ['set_pump_standby', [self.name, flow_path], {}]
        self._send_cmd(cmd, False)

    def _on_set_valve_position(self, evt):
        evt_obj = evt.GetEventObject()

        if evt_obj == self._buffer1_valve_ctrl:
            valve = 'buffer1'
            val = self._buffer1_valve_ctrl.GetValue()
        elif evt_obj == self._buffer2_valve_ctrl:
            valve = 'buffer2'
            val = self._buffer2_valve_ctrl.GetValue()
        elif evt_obj == self._purge1_valve_ctrl:
            valve = 'purge1'
            val = self._purge1_valve_ctrl.GetValue()
        elif evt_obj == self._purge2_valve_ctrl:
            valve = 'purge2'
            val = self._purge2_valve_ctrl.GetValue()
        elif evt_obj == self._selector_valve_ctrl:
            valve = 'selector'
            val = self._selector_valve_ctrl.GetValue()
        elif evt_obj == self._outlet_valve_ctrl:
            valve = 'outlet'
            val = self._outlet_valve_ctrl.GetValue()

        try:
            val = int(val)
        except ValueError:
            val = None

        if val is not None:
            cmd = ['set_valve_position', [self.name, valve, val], {}]
            self._send_cmd(cmd, False)

    def _on_stop_submission(self, evt):
        cmd = ['stop_sample_submission', [self.name,], {}]
        self._send_cmd(cmd, False)

    def _on_sampler_power_on(self, evt):
        cmd = ['set_autosampler_on', [self.name,], {}]
        self._send_cmd(cmd, False)

    def _set_status(self, cmd, val):
        if cmd == 'get_fast_hplc_status':
            inst_status = val['instrument_status']
            connected = str(inst_status['connected'])
            status = str(inst_status['status'])
            run_queue_status = str(inst_status['run_queue_status'])

            if connected != self._inst_connected:
                wx.CallAfter(self._inst_connected_ctrl.SetLabel,
                    connected)
                self._inst_connected = connected

            if status != self._inst_status:
                wx.CallAfter(self._inst_status_ctrl.SetLabel,
                    status)
                self._inst_status =  status

            if (run_queue_status != self._inst_run_queue_status):
                wx.CallAfter(self._inst_run_queue_status_ctrl.SetLabel,
                    run_queue_status)
                self._inst_run_queue_status = run_queue_status

            errors = inst_status['errors']
            if len(errors) == 0:
                error_status = 'None'
            else:
                error_status = 'Error'

            if error_status != self._inst_err_status:
                wx.CallAfter(self._inst_err_status_ctrl.SetLabel, error_status)
                self._inst_err_status = error_status

            err_string = ''
            for key, value in errors.items():
                err_string += '{}\n{}\n\n'.format(key, value)

            if err_string != self._inst_errs:
                wx.CallAfter(self._inst_errs_ctrl.SetValue, err_string)
                self._inst_errs = err_string


            pump_status = val['pump_status']
            pump1_purge = str(pump_status['purging_pump1'][0])
            pump1_purge_vol = str(round(float(pump_status['purging_pump1'][1]),3))
            pump1_eq = str(pump_status['equilibrate_pump1'][0])
            pump1_eq_vol = str(round(float(pump_status['equilibrate_pump1'][1]),3))
            pump1_flow = str(round(float(pump_status['flow1']),3))
            pump1_pressure = str(round(float(pump_status['pressure1']),3))

            if pump1_purge != self._pump1_purge:
                wx.CallAfter(self._pump1_purge_ctrl.SetLabel, pump1_purge)
                self._pump1_purge = pump1_purge

            if pump1_purge.lower() == 'false':
                pump1_purge_vol = '0.0'

            if pump1_purge_vol != self._pump1_purge_vol:
                wx.CallAfter(self._pump1_purge_vol_ctrl.SetLabel, pump1_purge_vol)
                self._pump1_purge_vol = pump1_purge_vol

            if pump1_eq != self._pump1_eq:
                wx.CallAfter(self._pump1_eq_ctrl.SetLabel, pump1_eq)
                self._pump1_eq = pump1_eq

            if pump1_eq.lower() == 'false':
                pump1_eq_vol = '0.0'

            if pump1_eq_vol != self._pump1_eq_vol:
                wx.CallAfter(self._pump1_eq_vol_ctrl.SetLabel, pump1_eq_vol)
                self._pump1_eq_vol = pump1_eq_vol

            if pump1_flow != self._pump1_flow:
                wx.CallAfter(self._pump1_flow_ctrl.SetLabel, pump1_flow)
                self._pump1_flow = pump1_flow

            if pump1_pressure != self._pump1_pressure:
                wx.CallAfter(self._pump1_pressure_ctrl.SetLabel, pump1_pressure)
                self._pump1_pressure = pump1_pressure

            if self._device_type == 'AgilentHPLC2Pumps':
                flow_path = str(pump_status['active_flow_path'])
                flow_path_status = str(pump_status['switching_flow_path'])

                if flow_path != self._flow_path:
                    wx.CallAfter(self._flow_path_ctrl.SetLabel, flow_path)
                    self._flow_path = flow_path

                if flow_path_status != self._flow_path_status:
                    wx.CallAfter(self._flow_path_status_ctrl.SetLabel,
                        flow_path_status)
                    self._flow_path_status = flow_path_status

                pump2_purge = str(pump_status['purging_pump2'][0])
                pump2_purge_vol = str(round(float(pump_status['purging_pump2'][1]),3))
                pump2_eq = str(pump_status['equilibrate_pump2'][0])
                pump2_eq_vol = str(round(float(pump_status['equilibrate_pump2'][1]),3))
                pump2_flow = str(round(float(pump_status['flow2']),3))
                pump2_pressure = str(round(float(pump_status['pressure2']),3))

                if pump2_purge != self._pump2_purge:
                    wx.CallAfter(self._pump2_purge_ctrl.SetLabel, pump2_purge)
                    self._pump2_purge = pump2_purge

                if pump2_purge.lower() == 'false':
                    pump2_purge_vol = '0.0'

                if pump2_purge_vol != self._pump2_purge_vol:
                    wx.CallAfter(self._pump2_purge_vol_ctrl.SetLabel, pump2_purge_vol)
                    self._pump2_purge_vol = pump2_purge_vol

                if pump2_eq != self._pump2_eq:
                    wx.CallAfter(self._pump2_eq_ctrl.SetLabel, pump2_eq)
                    self._pump2_eq = pump2_eq

                if pump2_eq.lower() == 'false':
                    pump2_eq_vol = '0.0'

                if pump2_eq_vol != self._pump2_eq_vol:
                    wx.CallAfter(self._pump2_eq_vol_ctrl.SetLabel, pump2_eq_vol)
                    self._pump2_eq_vol = pump2_eq_vol

                if pump2_flow != self._pump2_flow:
                    wx.CallAfter(self._pump2_flow_ctrl.SetLabel, pump2_flow)
                    self._pump2_flow = pump2_flow

                if pump2_pressure != self._pump2_pressure:
                    wx.CallAfter(self._pump2_pressure_ctrl.SetLabel, pump2_pressure)
                    self._pump2_pressure = pump2_pressure


            sampler_status = val['autosampler_status']
            submitting_sample = str(sampler_status['submitting_sample'])
            temperature = str(round(float(sampler_status['temperature']),3))

            if submitting_sample != self._sampler_submitting:
                wx.CallAfter(self._sampler_submitting_ctrl.SetLabel,
                    submitting_sample)
                self._sampler_submitting = submitting_sample

            if temperature != self._sampler_temp:
                wx.CallAfter(self._sampler_temp_ctrl.SetLabel,
                    temperature)
                self._sampler_temp = temperature


        elif cmd == 'get_slow_hplc_status':
            pump_status = val['pump_status']
            pump1_flow_target = str(round(float(pump_status['target_flow1']),3))
            pump1_flow_accel = str(round(float(pump_status['flow_accel1']),3))
            pump1_power = str(pump_status['power_status1'])
            pump1_pressure_lim = str(round(float(pump_status['high_pressure_lim1']),3))

            if pump1_flow_target != self._pump1_flow_target:
                wx.CallAfter(self._pump1_flow_target_ctrl.SetLabel,
                    pump1_flow_target)
                self._pump1_flow_target = pump1_flow_target

            if pump1_flow_accel != self._pump1_flow_accel:
                wx.CallAfter(self._pump1_flow_accel_ctrl.SetLabel,
                    pump1_flow_accel)
                self._pump1_flow_accel = pump1_flow_accel

            if pump1_power != self._pump1_power:
                wx.CallAfter(self._pump1_power_ctrl.SetLabel, pump1_power)
                self._pump1_power = pump1_power

            if pump1_pressure_lim != self._pump1_pressure_lim:
                wx.CallAfter(self._pump1_pressure_lim_ctrl.SetLabel, pump1_pressure_lim)
                self._pump1_pressure_lim = pump1_pressure_lim

            if self._device_type == 'AgilentHPLC2Pumps':
                pump2_flow_target = str(round(float(pump_status['target_flow2']),3))
                pump2_flow_accel = str(round(float(pump_status['flow_accel2']),3))
                pump2_power = str(pump_status['power_status2'])
                pump2_pressure_lim = str(round(float(pump_status['high_pressure_lim2']),3))

                if pump2_flow_target != self._pump2_flow_target:
                    wx.CallAfter(self._pump2_flow_target_ctrl.SetLabel,
                        pump2_flow_target)
                    self._pump2_flow_target = pump2_flow_target

                if pump2_flow_accel != self._pump2_flow_accel:
                    wx.CallAfter(self._pump2_flow_accel_ctrl.SetLabel,
                        pump2_flow_accel)
                    self._pump2_flow_accel = pump2_flow_accel

                if pump2_power != self._pump2_power:
                    wx.CallAfter(self._pump2_power_ctrl.SetLabel, pump2_power)
                    self._pump2_power = pump2_power

                if pump2_pressure_lim != self._pump2_pressure_lim:
                    wx.CallAfter(self._pump2_pressure_lim_ctrl.SetLabel,
                        pump2_pressure_lim)
                    self._pump2_pressure_lim = pump2_pressure_lim


            sampler_status = val['autosampler_status']
            thermostat_power = str(sampler_status['thermostat_power_status'])

            if thermostat_power != self._sampler_thermostat_power:
                wx.CallAfter(self._sampler_thermostat_power_ctrl.SetLabel,
                    thermostat_power)
                self._sampler_thermostat_power = thermostat_power


        elif cmd == 'get_valve_status':
            buffer1 = int(val['buffer1'])
            purge1 = int(val['purge1'])

            if buffer1 != self._buffer1_valve:
                wx.CallAfter(self._buffer1_valve_ctrl.SafeChangeValue, buffer1)
                self._buffer1_valve = buffer1

            if purge1 != self._purge1_valve:
                wx.CallAfter(self._purge1_valve_ctrl.SafeChangeValue, purge1)
                self._purge1_valve = purge1

            if self._device_type == 'AgilentHPLC2Pumps':
                buffer2 = int(val['buffer2'])
                purge2 = int(val['purge2'])
                selector = int(val['selector'])
                outlet = int(val['outlet'])

                if buffer2 != self._buffer2_valve:
                    wx.CallAfter(self._buffer2_valve_ctrl.SafeChangeValue,
                        buffer2)
                    self._buffer2_valve = buffer2

                if purge2 != self._purge2_valve:
                    wx.CallAfter(self._purge2_valve_ctrl.SafeChangeValue,
                        purge2)
                    self._purge2_valve = purge2

                if selector != self._selector_valve:
                    wx.CallAfter(self._selector_valve_ctrl.SafeChangeValue,
                        selector)
                    self._selector_valve = selector

                if outlet != self._outlet_valve:
                    wx.CallAfter(self._outlet_valve_ctrl.SafeChangeValue,
                        outlet)
                    self._outlet_valve = outlet

class HPLCFrame(utils.DeviceFrame):
    """
    A lightweight frame allowing one to work with arbitrary number of HPLCs.
    Only meant to be used when the hplccon module is run directly,
    rather than when it is imported into another program.
    """
    def __init__(self, name, settings, *args, **kwargs):
        """
        Initializes the HPLC frame. Takes args and kwargs for the wx.Frame class.
        """
        super(HPLCFrame, self).__init__(name, settings, HPLCPanel,
            *args, **kwargs)

        # Enable these to init devices on startup
        self.setup_devices = self.settings.pop('device_init', None)

        self._init_devices()


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

    buffer1_valve_args = {
        'name'  : 'Buffer 1',
        'args'  : ['Cheminert', 'COM3'],
        'kwargs': {'positions' : 10}
        }

    buffer2_valve_args = {
        'name'  : 'Buffer 2',
        'args'  : ['Cheminert', 'COM4'],
        'kwargs': {'positions' : 10}
        }

    # my_hplc = AgilentHPLC2Pumps(hplc_args['name'], None, hplc_args=hplc_args,
    #     selector_valve_args=selector_valve_args,
    #     outlet_valve_args=outlet_valve_args,
    #     purge1_valve_args=purge1_valve_args,
    #     purge2_valve_args=purge2_valve_args,
    #     buffer1_valve_args=buffer1_valve_args,
    #     buffer2_valve_args=buffer2_valve_args,
    #     pump1_id='quat. pump 1#1c#1',
    #     pump2_id='quat. pump 2#1c#2')

    # print('waiting to connect')
    # while not my_hplc.get_connected():
    #     time.sleep(0.1)

    # time.sleep(1)

    #SEC-SAXS
    # seq_sample1 = {
    #     'acq_method'    : 'SECSAXS_test',
    #     'sample_loc'    : 'D2F-A1',
    #     'injection_vol' : 10.0,
    #     'sample_name'   : 'test1',
    #     'sample_descrip': 'test',
    #     'sample_type'   : 'Sample',
    #     }

    # sample_list = [seq_sample1]
    # result_path = 'api_test'
    # result_name = '<D>-api_test_seq'

    # my_hplc.submit_sequence('test_seq', sample_list, result_path, result_name)


    # my_hplc.submit_hplc_sample('test', 'SECSAXS_test', 'D2F-A1', 10.0,
    #     0.05, 0.1, 0.1, 60.0, result_path='api_test', )


    setup_devices = [
        {'name': 'SEC-SAXS', 'args': ['AgilentHPLC2Pumps', None],
            'kwargs': {'hplc_args' : hplc_args,
            'selector_valve_args' : selector_valve_args,
            'outlet_valve_args' : outlet_valve_args,
            'purge1_valve_args' : purge1_valve_args,
            'purge2_valve_args' : purge2_valve_args,
            'buffer1_valve_args' : buffer1_valve_args,
            'buffer2_valve_args' : buffer2_valve_args,
            'pump1_id' : 'quat. pump 1#1c#1',
            'pump2_id' : 'quat. pump 2#1c#2'},
        }
        ]

    # Local
    com_thread = HPLCCommThread('HPLCComm')
    com_thread.start()

    # # Remote
    # com_thread = None

    settings = {
        'remote'        : False,
        'remote_device' : 'hplc',
        'device_init'   : setup_devices,
        'remote_ip'     : '192.168.1.16',
        'remote_port'   : '5558',
        'com_thread'    : com_thread
        }

    app = wx.App()
    logger.debug('Setting up wx app')
    frame = HPLCFrame('HPLCFrame', settings, parent=None, title='HPLC Control')
    frame.Show()
    app.MainLoop()

    if com_thread is not None:
        com_thread.stop()
        com_thread.join()
