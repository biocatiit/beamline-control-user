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
import wx.lib.mixins.listctrl
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


class AgilentHPLCStandard(AgilentHPLC):
    """
    Specific control for a standard Agilent stack with one pump
    """

    def __init__(self, name, device, hplc_args={}, purge1_valve_args={},
        buffer1_valve_args={}, pump1_id='', connect_valves=True):
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
        self._equil_flow1 = False

        self._buffer_monitor1 = utils.BufferMonitor(self._get_flow_rate1)

        # Connect valves
        if connect_valves:
            # Defines valve positions for various states
            self._purge_positions = {
                1   : {'purge1': 2},
                }

            self._column_positions = {
                1   : {'purge1': 1},
                }

            self._active_flow_path = None
            self._purging_flow1 = False
            self._purging_flow2 = False
            self._equil_flow2 = False

            self._connect_valves(purge1_valve_args, buffer1_valve_args)

        # Connect HPLC
        self._pump1_id = pump1_id

        hplc_device_type = hplc_args['args'][0]
        hplc_device = hplc_args['args'][1]
        hplc_kwargs = hplc_args['kwargs']

        AgilentHPLC.__init__(self, name, hplc_device, **hplc_kwargs)

        while not self.get_connected():
            time.sleep(0.1)

        # Other definitions
        self._default_purge_rate = 5.0 #mL/min
        self._default_purge_accel = 10.0 #mL/min
        self._default_purge_max_pressure = 250.0 #bar
        self._pre_purge_flow1 = None
        self._pre_purge_flow2 = None
        self._pre_purge_flow_accel1 = 0.0
        self._pre_purge_flow_accel2 = 0.0
        self._pre_purge_max_pressure1 = 0.0
        self._pre_purge_max_pressure2 = 0.0
        self._remaining_purge1_vol = 0.0
        self._remaining_purge2_vol = 0.0
        self._target_purge_flow1 = 0.0
        self._target_purge_flow2 = 0.0
        self._target_purge_accel1 = 0.0
        self._target_purge_accel2 = 0.0
        self._purge_max_pressure1 = 0.0
        self._purge_max_pressure2 = 0.0
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

        self._submitting_sample = False
        self._submit_queue = deque()

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

        self._uv_abs_traces = {}
        traces = self.get_available_data_traces()

        for trace in traces:
            if trace.startswith('MWD: Signal'):
                wav = trace.split('=')[1].strip()

                if wav.lower() != 'off':
                    wav = wav.split(' ')[0]
                    wav = float(wav)
                    self._uv_abs_traces[wav] = trace

    def  connect(self):
        """
        Expected by the thread, but connection is don on init, so this does nothing
        """
        pass

    def _connect_valves(self, p1_args, b1_args):

        p1_name = p1_args['name']
        p1_arg_list = p1_args['args']
        p1_kwarg_list = p1_args['kwargs']
        p1_device_type = p1_arg_list[0]
        p1_comm = p1_arg_list[1]

        self._purge1_valve = valvecon.known_valves[p1_device_type](p1_name,
            p1_comm, **p1_kwarg_list)
        self._purge1_valve.connect()

        b1_name = b1_args['name']
        b1_arg_list = b1_args['args']
        b1_kwarg_list = b1_args['kwargs']
        b1_device_type = b1_arg_list[0]
        b1_comm = b1_arg_list[1]

        self._buffer1_valve = valvecon.known_valves[b1_device_type](b1_name,
            b1_comm, **b1_kwarg_list)
        self._buffer1_valve.connect()

        self._valves = {
            'purge1'    : self._purge1_valve,
            'buffer1'   : self._buffer1_valve,
            }

        self._active_flow_path = 1

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
        position = None
        retry = 5
        while position is None and retry > 0:
            position = valve.get_position()
            retry -= 1

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
        flow_rate = self.get_data_trace('Quat. Pump: Flow (mL/min)')[1][-1]

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

        pressure = self.get_data_trace('Quat. Pump: Pressure (bar)')[1][-1]

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

    def get_hplc_pump_power_status(self, flow_path, update=True):
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
            pump_power_status = self.get_pump_power_status(self._pump1_id,
                update)
        elif flow_path == 2:
            pump_power_status = self.get_pump_power_status(self._pump2_id,
                update)

        return pump_power_status

    def get_hplc_seal_wash_settings(self, flow_path):
        """
        Gets the pump seal wash settings of the specified flow path

        Parameters
        ----------
        flow_path: int
            The flow path to get the seal wash settings for. Either 1 or 2.

        Returns
        -------
        wash_settings: dict
            The seal wash settings. Has keys 'mode', 'single_duration',
            'period', 'period_duration'. Returns an empty dictionary if
            it fails.
        """
        flow_path = int(flow_path)

        if flow_path == 1:
            pump_power_status = self.get_seal_wash_settings(self._pump1_id)
        elif flow_path == 2:
            pump_power_status = self.get_seal_wash_settings(self._pump2_id)

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

    def get_hplc_uv_abs(self, wav):
        """
        Gets the uv absorbance at the specified wavelength, if available)

        Parameters
        ----------
        wav: float
            The wavelength to the the absorbance for.

        Returns
        -------
        uv_abs: float
            The uv absorbance. Returns None if wavelength is not available.
        """
        wav = float(wav)
        if wav in self._uv_abs_traces:
            trace = self._uv_abs_traces[wav]
            uv_abs = float(self.get_data_trace(trace)[1][-1])
        else:
            uv_abs = None

        return uv_abs

    def get_hplc_elapsed_runtime(self, update=True):
        """
        Gets the elasped runtime of the specified flow path

        Parameters
        ----------
        update: bool

        Returns
        -------
        run_time: float
            The elasped runtime of the specified flow path. Returns -1
            if time cannot be acquired.
        """

        if self._active_flow_path == 1:
            run_time = self.get_elapsed_runtime(self._pump1_id,
                update)
        elif self._active_flow_path == 2:
            run_time = self.get_elapsed_runtime(self._pump2_id,
                update)

        return run_time

    def get_hplc_total_runtime(self, update=True):
        """
        Gets the total runtime of the specified flow path

        Parameters
        ----------
        update: bool

        Returns
        -------
        run_time: float
            The total runtime of the specified flow path. Returns -1
            if time cannot be acquired.
        """

        if self._active_flow_path == 1:
            run_time = self.get_total_runtime(self._pump1_id,
                update)
        elif self._active_flow_path == 2:
            run_time = self.get_total_runtime(self._pump2_id,
                update)

        return run_time

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
        equil_with_sample=False, stop_after_equil=True, purge_max_pressure=None):
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
        purge_max_pressure: float
            Maximum pressure during purging. If no pressure is supplied, the
            device's default purge max pressure is used.
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
                    equil_with_sample, stop_after_equil, purge_max_pressure)

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
        stop_after_equil, purge_max_pressure):
        if purge_rate is None:
            purge_rate = self._default_purge_rate
        if purge_accel is None:
            purge_accel = self._default_purge_accel
        if purge_max_pressure is None:
            purge_max_pressure = self._default_purge_max_pressure

        if flow_path == 1:
            self._equil1_args = {
                'equil_rate'    : equil_rate,
                'equil_accel'   : equil_accel,
                'purge'         : purge,
                'purge_volume'  : purge_volume,
                'purge_rate'    : purge_rate,
                'purge_accel'   : purge_accel,
                'purge_max_pressure': purge_max_pressure,
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
                'purge_max_pressure': purge_max_pressure,
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
                purge_max_pressure1 = self._equil1_args['purge_max_pressure']
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
                purge_max_pressure2 = self._equil2_args['purge_max_pressure']
                stop_after_equil2 = self._equil2_args['stop_after_equil']

                self.set_hplc_flow_accel(equil_accel2, 2)


            if start_purge1:
                if purge1:
                    self.purge_flow_path(1, purge_volume1, purge_rate1,
                        purge_accel1, False, True,
                        purge_max_pressure=purge_max_pressure1)

                    while not self._purge1_ongoing.is_set():
                        time.sleep(0.1)

                start_purge1 = False
                monitor_purge1 = True

            if start_purge2:
                if purge2:
                    self.purge_flow_path(2, purge_volume2, purge_rate2,
                        purge_accel2, False, True,
                        purge_max_pressure=purge_max_pressure2)

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
        stop_after_purge=True, purge_max_pressure=None):
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
        purge_max_pressure: float
            Maximum pressure during purging. If no pressure is supplied, the
            device's default purge max pressure is used.
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
                    stop_after_purge, purge_max_pressure)

                success =  True

            else:
                success = False

        return success

    def _start_purge(self, flow_path, purge_volume, purge_rate, purge_accel,
            restore_flow_after_purge, stop_before_purge, stop_after_purge,
            purge_max_pressure):
        if purge_rate is None:
            purge_rate = self._default_purge_rate
        if purge_accel is None:
            purge_accel = self._default_purge_accel
        if purge_max_pressure is None:
            purge_max_pressure = self._default_purge_max_pressure

        if flow_path == 1:
            if restore_flow_after_purge:
                self._pre_purge_flow1 = self.get_hplc_flow_rate(1)
            else:
                self._pre_purge_flow1 = None

            self._pre_purge_flow_accel1 = self.get_hplc_flow_accel(1)
            self._pre_purge_max_pressure1 = self.get_hplc_high_pressure_limit(1,
                update_method=False)
            self._remaining_purge1_vol = float(purge_volume)
            self._target_purge_flow1 = float(purge_rate)
            self._target_purge_accel1 = float(purge_accel)
            self._purge_max_pressure1 = float(purge_max_pressure)
            self._stop_before_purging1 = stop_before_purge
            self._stop_after_purging1 = stop_after_purge
            self._purging_flow1 = True
            self._purge1_ongoing.set()

        elif flow_path == 2:
            if restore_flow_after_purge:
                self._pre_purge_flow2 = self.get_hplc_flow_rate(2)
            else:
                self._pre_purge_flow2 = None

            self._pre_purge_flow_accel2 = self.get_hplc_flow_accel(2)
            self._pre_purge_max_pressure2 = self.get_hplc_high_pressure_limit(2,
                update_method=False)
            self._remaining_purge2_vol = float(purge_volume)
            self._target_purge_flow2 = float(purge_rate)
            self._target_purge_accel2 = float(purge_accel)
            self._purge_max_pressure2 = float(purge_max_pressure)
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
                and not stopping_flow1 and not stopping_initial_flow1
                and self._purge1_ongoing.is_set()):
                stopping_initial_flow1 = True
                monitoring_flow1 = False
                stopping_flow1 = False

                if self._stop_before_purging1:
                    self.set_hplc_flow_rate(0, 1)

                if self._pre_purge_flow1 is None:
                    final_flow1 = 0
                else:
                    final_flow1 = self._pre_purge_flow1

            if (self._purging_flow2 and not monitoring_flow2
                and not stopping_flow2 and not stopping_initial_flow2
                and self._purge2_ongoing.is_set()):
                stopping_initial_flow2 = True
                monitoring_flow2 = False
                stopping_flow2 = False

                if self._stop_before_purging2:
                    self.set_hplc_flow_rate(0, 2)

                if self._pre_purge_flow2 is None:
                        final_flow2 = 0
                else:
                    final_flow2 = self._pre_purge_flow2


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

                    self.set_hplc_flow_accel(self._target_purge_accel1, 1)
                    self.set_hplc_high_pressure_limit(self._purge_max_pressure1,
                        1)

                    flow_accel1 = self.get_hplc_flow_accel(1)
                    previous_flow1 = self.get_hplc_flow_rate(1)
                    previous_time1 = time.time()
                    update_time1 = previous_time1

                    self.set_hplc_flow_rate(self._target_purge_flow1, 1)

                    stopping_initial_flow1 = False
                    monitoring_flow1 = True

                elif self._remaining_purge1_vol <= 0:
                    #Aborted
                    stopping_initial_flow1 = False
                    monitoring_flow1 = True
                    flow_accel1 = 0
                    previous_flow1 = 0
                    previous_time1 = time.time()
                    update_time1 = previous_time1

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

                    self.set_hplc_flow_accel(self._target_purge_accel2, 2)
                    self.set_hplc_high_pressure_limit(self._purge_max_pressure2,
                        2)

                    flow_accel2 = self.get_hplc_flow_accel(2)
                    previous_flow2 = self.get_hplc_flow_rate(2)
                    previous_time2 = time.time()
                    update_time2 = previous_time2

                    self.set_hplc_flow_rate(self._target_purge_flow2, 2)

                    stopping_initial_flow2 = False
                    monitoring_flow2 = True

                elif self._remaining_purge2_vol <= 0:
                    #Aborted
                    stopping_initial_flow2 = False
                    monitoring_flow2 = True
                    flow_accel2 = 0
                    previous_flow2 = 0
                    previous_time2 = time.time()
                    update_time2 = previous_time1


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
                        self.set_hplc_flow_rate(0, 1)
                    else:
                        self.set_hplc_flow_rate(final_flow1,1)

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
                        self.set_hplc_flow_rate(0,2)
                    else:
                        self.set_hplc_flow_rate(final_flow2, 2)

                if current_time2 - update_time2 > 15:
                    update_time2 = current_time2


            if stopping_flow1:
                current_flow1 = self.get_hplc_flow_rate(1)
                current_time1 = time.time()

                purge_not_started1 = True

                for name, pos in self._column_positions[1].items():
                    current_pos = int(self.get_valve_position(name))

                    if current_pos != pos:
                        purge_not_started1 = False
                        break

                if ((self._stop_after_purging1 and current_flow1 == 0)
                    or (not self._stop_after_purging1
                    and round(current_flow1, 3) == round(final_flow1, 3))
                    or purge_not_started1):
                    self.set_hplc_flow_accel(self._pre_purge_flow_accel1, 1)
                    self.set_hplc_high_pressure_limit(
                        self._pre_purge_max_pressure1, 1)

                    for name, pos in self._column_positions[1].items():
                        current_pos = int(self.get_valve_position(name))

                        if current_pos != pos:
                            self.set_valve_position(name, pos)

                    if self._stop_after_purging1:
                        self.set_hplc_flow_rate(final_flow1, 1)

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

                purge_not_started2 = True

                for name, pos in self._column_positions[2].items():
                    current_pos = int(self.get_valve_position(name))

                    if current_pos != pos:
                        purge_not_started2 = False
                        break

                if ((self._stop_after_purging2 and current_flow2 == 0)
                    or (not self._stop_after_purging2
                    and round(current_flow2, 3) == round(final_flow2, 3))
                    or purge_not_started2):
                    self.set_hplc_flow_accel(self._pre_purge_flow_accel2, 2)
                    self.set_hplc_high_pressure_limit(
                        self._pre_purge_max_pressure2, 2)

                    for name, pos in self._column_positions[2].items():
                        current_pos = int(self.get_valve_position(name))

                        if current_pos != pos:
                            self.set_valve_position(name, pos)

                    if self._stop_after_purging2:
                        self.set_hplc_flow_rate(final_flow2, 2)

                    stopping_flow2 = False
                    self._purging_flow2 = False
                    self._purge2_ongoing.clear()

                    logger.info(('HPLC %s finished purging flow path 2. '
                        'Flow rate set to %s'), self.name, final_flow2)

                if current_time2 - update_time2 > 15:
                    update_time2 = current_time2


            if (not self._purge1_ongoing.is_set()
                and not self._purge2_ongoing.is_set()):
                self._monitor_purge_evt.clear()
                monitoring_flow1 = False
                monitoring_flow2 = False
                stopping_flow1 = False
                stopping_flow2 = False
                stopping_initial_flow1 = False
                stopping_initial_flow2 = False
                self._remaining_purge1_vol = 0
                self._remaining_purge2_vol = 0
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

    def set_hplc_seal_wash_settings(self, flow_path, mode, single_duration=0., period=0.,
        period_duration=0.):
        """
        Sets the pump seal wash settings on the specified flow path. This can
        be used to turn off the pump wash by setting the mode to 'Off'.

        Parameters
        ----------
        mode: str
            Either 'Off', 'Single', or 'Periodic'
        single_duration: float
            The duration in minutes of a single wash. Only used if the
            mode is set to Single
        period: float
            The period in minutes between washes. Only used if the mode is
            set to Periodic
        period_duration: float
            The duration in minutes of the wash every period. Only used if
            the mode is set to Periodic.
        flow_path: int
            The flow path to stop the purge on. Either 1 or 2.

        Returns
        -------
        success: bool
            True if successful.
        """
        flow_path = int(flow_path)
        if mode == 'Single':
            single_duration = float(single_duration)
        else:
            single_duration = 0.
        if mode == 'Periodic':
            period = float(period)
            period_duration = float(period_duration)
        else:
            period = 0.
            period_duration = 0.

        if flow_path == 1:
            pump_id = self._pump1_id
        elif flow_path == 2:
            pump_id = self._pump2_id

        success = self.set_seal_wash_settings(mode, single_duration, period,
            period_duration, pump_id)

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

    def set_hplc_pump_off(self, flow_path):
        """
        Turns the pump on the specified flow path to off.

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

        success = self.set_pump_off(pump_id)

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
            The flow path to set the active buffer for. Either 1 or 2.
        """
        flow_path = int(flow_path)
        if flow_path == 1:
            self._buffer_monitor1.set_active_buffer_position(position)
        elif flow_path == 2:
            self._buffer_monitor2.set_active_buffer_position(position)

        return True

    def remove_buffer(self, position, flow_path):
        """
        Removes the buffer at the given position.

        Parameters
        ----------
        position: str
            The buffer position (e.g. 1 or A)
        flow_path: int
            The flow path to remove the buffer for. Either 1 or 2.
        """
        flow_path = int(flow_path)
        if flow_path == 1:
            self._buffer_monitor1.remove_buffer(position)
        elif flow_path == 2:
            self._buffer_monitor2.remove_buffer(position)

        return True

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
        self._submitting_sample = True

        flow_rate = float(flow_rate)
        flow_accel = float(flow_accel)
        high_pressure_lim = float(high_pressure_lim)
        inj_vol = float(inj_vol)

        inst_status = self.get_instrument_status()
        if (inst_status == 'Offline' or inst_status == 'Unknown'
            or inst_status == 'Error' or inst_status == 'Idle' or
            inst_status == 'NotReady' or inst_status == 'Standby'):
            self.set_hplc_flow_accel(flow_accel, self._active_flow_path)
            self.set_hplc_flow_rate(flow_rate, self._active_flow_path)

        if self._active_flow_path == 1:
            active_pump_id = self._pump1_id

        stop_time = total_elution_vol/flow_rate

        self.get_current_method_from_instrument()

        acq_pump_method_vals = {
            'Flow': flow_rate,
            'MaximumFlowRamp': flow_accel,
            'HighPressureLimit': high_pressure_lim,
            'StopTime_Time': stop_time,
            }

        self.load_method(acq_method)
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

        submit_args = {
            'name'                  : name,
            'sequence_vals'         : sequence_vals,
            'result_path'           : result_path,
            'flow_rate'             : flow_rate,
            'wait_for_flow_ramp'    : wait_for_flow_ramp,
            'settle_time'           : settle_time,
            }

        self._submit_queue.append(submit_args)

        logger.info(('HPLC %s starting to submit sample %s on active flow '
            'path %s'), self.name, name, self._active_flow_path)


        self._abort_submit.clear()
        self._monitor_submit_evt.set()

        return True

    def _monitor_submit(self):
        while not self._terminate_monitor_submit.is_set():
            self._monitor_submit_evt.wait()

            if (self._abort_submit.is_set()
                and self._terminate_monitor_submit.is_set()):
                break

            submit_args = self._submit_queue.popleft()
            name = submit_args['name']
            sequence_vals = submit_args['sequence_vals']
            result_path = submit_args['result_path']
            flow_rate = round(submit_args['flow_rate'], 3)
            wait_for_flow_ramp = submit_args['wait_for_flow_ramp']
            settle_time = submit_args['settle_time']

            if wait_for_flow_ramp:
                while (round(self.get_hplc_flow_rate(self._active_flow_path), 3)
                    != flow_rate):
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

            if len(self._submit_queue) == 0:
                if self.get_run_queue_status() == 'Default':
                    while True:
                        status = self.get_instrument_status()
                        if (status != 'Run' and status != 'Injecting'
                            and status != 'PostRun' and status != 'PreRun'):
                            time.sleep(0.1)
                        else:
                            break

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
            self._submit_queue.clear()
            self._abort_submit.set()
            logger.info('HPLC %s aborted sample submission', self.name)

    def stop_all(self):
        """
        Stops all current actions, including purging, switching, submitting a
        sample. Pauses the run queue and aborts the current run.
        """
        self.stop_purge(1)
        self.stop_submit_sample()
        self.pause_run_queue()
        try:
            self.abort_current_run()
        except Exception:
            pass
        self.set_hplc_flow_rate(0, 1)

    def stop_all_immediately(self):
        """
        Stops all current actions, including purging, switching, submitting a
        sample. Pauses the run queue and aborts the current run. Sets the flow
        acceleration to max to stop the pumps as quickly as possible.
        """
        flow_accel1 = self.get_hplc_flow_accel(1)

        self.stop_all()

        self.set_hplc_flow_accel(100, 1)

        pump1_stopped = False
        while not pump1_stopped:
            if float(self.get_hplc_flow_rate(1)) == 0:
                pump1_stopped = True

        self.set_hplc_flow_accel(flow_accel1, 1)

    def stop_pump1(self):
        """
        Stops pump 1.
        """
        self.stop_purge(1)
        self.set_hplc_flow_rate(0, 1)

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

    def disconnect_all(self):
        """
        Use this method instead of disconnect to disconnect from both the
        valves and the HPLC.
        """
        self._buffer_monitor1.stop_monitor()

        for valve in self._valves.values():
            valve.disconnect()

        self._terminate_monitor_purge.set()
        self._monitor_purge_evt.set()
        self._monitor_purge_thread.join()

        self._abort_submit.set()
        self._terminate_monitor_submit.set()
        self._monitor_submit_evt.set()
        self._monitor_submit_thread.join()

        self.disconnect()

class AgilentHPLC2Pumps(AgilentHPLCStandard):
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

        self._buffer_monitor2 = utils.BufferMonitor(self._get_flow_rate2)

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

        self._active_flow_path = None
        self._purging_flow1 = False
        self._purging_flow2 = False
        self._equil_flow2 = False

        # Connect valves
        self._connect_valves(selector_valve_args, outlet_valve_args,
            purge1_valve_args, purge2_valve_args, buffer1_valve_args,
            buffer2_valve_args)

        AgilentHPLCStandard.__init__(self, name, device, hplc_args=hplc_args,
            purge1_valve_args=purge1_valve_args,
            buffer1_valve_args=buffer1_valve_args, pump1_id=pump1_id,
            connect_valves=False)

        # Connect HPLC
        self._pump2_id = pump2_id


        self._switching_flow_path = False

        self._monitor_switch_evt = threading.Event()
        self._terminate_monitor_switch = threading.Event()
        self._abort_switch = threading.Event()
        self._monitor_switch_thread = threading.Thread(
            target=self._monitor_switch)
        self._monitor_switch_thread.daemon = True
        self._monitor_switch_thread.start()


        self.set_active_buffer_position(self.get_valve_position('buffer2'), 2)

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

    def set_active_flow_path(self, flow_path, stop_flow1=False,
        stop_flow2=False, restore_flow_after_switch=True, purge_active=True,
        purge_volume=1.0, purge_rate=None, purge_accel=None,
        switch_with_sample=False, purge_max_pressure=None):
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
        purge_max_pressure: float
            Maximum pressure during purging. If no pressure is supplied, the
            device's default purge max pressure is used.
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
                    'purge_max_pressure': purge_max_pressure,
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
            purge_max_pressure = self._switch_args['purge_max_pressure']
            switch_with_sample = self._switch_args['switch_with_sample']

            initial_flow1 = self.get_hplc_flow_rate(1)
            initial_flow2 = self.get_hplc_flow_rate(2)

            if not self._abort_switch.is_set():
                if stop_flow1:
                    self.set_hplc_flow_rate(0, 1)

                if stop_flow2:
                    self.set_hplc_flow_rate(0, 2)

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

                if flow_path == 1:
                    self.set_autosampler_linked_pump(self._pump1_id)
                elif flow_path == 2:
                    self.set_autosampler_linked_pump(self._pump2_id)

                if purge_active:
                    if flow_path == 1:
                        stop_before_purge = stop_flow1
                        stop_after_purge = stop_flow1
                    elif flow_path == 2:
                        stop_before_purge = stop_flow2
                        stop_after_purge = stop_flow2

                    self.purge_flow_path(flow_path, purge_volume, purge_rate,
                        purge_accel, True, switch_with_sample, stop_before_purge,
                        stop_after_purge, purge_max_pressure=purge_max_pressure)

                    if restore_flow_after_switch:
                        if flow_path == 1:
                            self._pre_purge_flow1 = initial_flow1
                            self.set_hplc_flow_rate(initial_flow2, 2)

                        elif flow_path == 2:
                            self._pre_purge_flow2 = initial_flow2
                            self.set_hplc_flow_rate(initial_flow1, 1)

                elif restore_flow_after_switch:
                    self.set_hplc_flow_rate(initial_flow1, 1)
                    self.set_hplc_flow_rate(initial_flow2, 2)

            elif self._abort_switch.is_set() and restore_flow_after_switch:
                self.set_hplc_flow_rate(initial_flow1, 1)
                self.set_hplc_flow_rate(initial_flow2, 2)

            self._switching_flow_path = False
            self._monitor_switch_evt.clear()

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
        self._submitting_sample = True

        flow_rate = float(flow_rate)
        flow_accel = float(flow_accel)
        high_pressure_lim = float(high_pressure_lim)
        inj_vol = float(inj_vol)

        inst_status = self.get_instrument_status()
        if (inst_status == 'Offline' or inst_status == 'Unknown'
            or inst_status == 'Error' or inst_status == 'Idle' or
            inst_status == 'NotReady' or inst_status == 'Standby'):
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

        submit_args = {
            'name'                  : name,
            'sequence_vals'         : sequence_vals,
            'result_path'           : result_path,
            'flow_rate'             : flow_rate,
            'wait_for_flow_ramp'    : wait_for_flow_ramp,
            'settle_time'           : settle_time,
            }

        self._submit_queue.append(submit_args)

        logger.info(('HPLC %s starting to submit sample %s on active flow '
            'path %s'), self.name, name, self._active_flow_path)


        self._abort_submit.clear()
        self._monitor_submit_evt.set()

        return True

    def stop_switch(self):
        """
        Stops switching active flow path
        """
        if self._switching_flow_path:
            self._abort_switch.set()
            logger.info('HPLC %s stoping switching of active flow path', self.name)

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

    def stop_pump2(self):
        """
        Stops pump 2.
        """
        self.stop_purge(2)
        self.set_hplc_flow_rate(0, 2)

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
    'AgilentHPLCStandard'   : AgilentHPLCStandard,
    'AgilentHPLC2Pumps'     : AgilentHPLC2Pumps,
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
            'get_very_slow_hplc_status' : self._get_very_slow_hplc_status,
            'get_valve_status'          : self._get_valve_status,
            'set_valve_position'        : self._set_valve_position,
            'purge_flow_path'           : self._purge_flow_path,
            'equil_flow_path'           : self._equil_flow_path,
            'set_active_flow_path'      : self._set_active_flow_path,
            'set_flow_rate'             : self._set_flow_rate,
            'set_flow_accel'            : self._set_flow_accel,
            'set_high_pressure_lim'     : self._set_high_pressure_lim,
            'set_seal_wash_settings'    : self._set_pump_seal_wash_settings,
            'set_pump_on'               : self._set_pump_on,
            'set_pump_standby'          : self._set_pump_standby,
            'set_pump_off'              : self._set_pump_off,
            'set_autosampler_on'        : self._set_autosampler_on,
            'set_autosampler_therm_on'  : self._set_autosampler_therm_on,
            'set_autosampler_therm_off' : self._set_autosampler_therm_off,
            'set_autosampler_temp'      : self._set_autosampler_temp,
            'set_mwd_on'                : self._set_mwd_on,
            'set_uv_lamp_on'            : self._set_uv_lamp_on,
            'set_uv_lamp_off'           : self._set_uv_lamp_off,
            'set_vis_lamp_on'           : self._set_vis_lamp_on,
            'set_vis_lamp_off'          : self._set_vis_lamp_off,
            'set_buffer_info'           : self._set_buffer_info,
            'remove_buffer'             : self._remove_buffer,
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
        device_names = copy.deepcopy(list(self._connected_devices.keys()))
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

        status = device.get_instrument_status()

        # if status == 'PreRun' or status == 'Injecting' or status == 'PostRun':
        #     # This seems to get hung up when the instrument is busy, so just
        #     # don't bother it when it's running
        #     return

        # a = time.time()
        instrument_status = {
            'status'            : status,
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

        if (isinstance(device, AgilentHPLCStandard)
            and not isinstance(device, AgilentHPLC2Pumps)):
            uv_status = {
                'uv_280_abs'    : device.get_hplc_uv_abs(280.0),
                'uv_260_abs'    : device.get_hplc_uv_abs(260.0),
                }

        else:
            uv_status = {}

        val = {
            'instrument_status' : instrument_status,
            'pump_status'       : pump_status,
            'autosampler_status': autosampler_status,
            'uv_status'         : uv_status,
        }

        self._return_value((name, cmd, val), comm_name)

        # print(time.time()-a)

        logger.debug("%s fast status: %s", name, val)

    def _get_slow_hplc_status(self, name, **kwargs):
        logger.debug("Getting %s slow status", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]

        status = device.get_instrument_status()
        sample_submission = device.get_submitting_sample_status()

        if (status == 'PreRun' or status == 'Injecting' or status == 'PostRun'
            or sample_submission):
            # This seems to get hung up when the instrument is busy, so just
            # don't bother it when it's running
            return

        instrument_status = {
            'elapsed_runtime'   : 0,
            'total_runtime'     : 0,
            'status'            : status,
            }

        if status == 'Run':
            instrument_status['elapsed_runtime'] = device.get_hplc_elapsed_runtime()
            instrument_status['total_runtime'] = device.get_hplc_total_runtime( update=False)

        pump_status = {
            'target_flow1'      : device.get_hplc_target_flow_rate(1),
            'flow_accel1'       : device.get_hplc_flow_accel(1, False),
            }

        if isinstance(device, AgilentHPLC2Pumps):
            pump_status['target_flow2'] = device.get_hplc_target_flow_rate(2)
            pump_status['flow_accel2'] = device.get_hplc_flow_accel(2, False)

        autosampler_status = {
            }

        if (isinstance(device, AgilentHPLCStandard)
            and not isinstance(device, AgilentHPLC2Pumps)):
            uv_status = {
                }

        else:
            uv_status = {}

        val = {
            'instrument_status' : instrument_status,
            'pump_status'       : pump_status,
            'autosampler_status': autosampler_status,
            'uv_status'         : uv_status,
        }

        self._return_value((name, cmd, val), comm_name)

        logger.debug("%s slow status: %s", name, val)

    def _get_very_slow_hplc_status(self, name, **kwargs):
        logger.debug("Getting %s very slow status", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]

        status = device.get_instrument_status()
        sample_submission = device.get_submitting_sample_status()

        if (status == 'PreRun' or status == 'Injecting' or status == 'PostRun'
            or sample_submission):
            # This seems to get hung up when the instrument is busy, so just
            # don't bother it when it's running
            return

        pump_status = {
            'power_status1'     : device.get_hplc_pump_power_status(1),
            'high_pressure_lim1': device.get_hplc_high_pressure_limit(1, False),
            }

        if isinstance(device, AgilentHPLC2Pumps):
            pump_status['power_status2'] = device.get_hplc_pump_power_status(2)
            pump_status['high_pressure_lim2'] =device.get_hplc_high_pressure_limit(2,
                False)
            # pump_status['seal_wash1'] = device.get_hplc_seal_wash_settings(1)
            # pump_status['seal_wash2'] = device.get_hplc_seal_wash_settings(2)

        autosampler_status = {
            'thermostat_power_status'   : device.get_autosampler_thermostat_power_status(),
            'temperature_setpoint'      : device.get_autosampler_temperature_set_point(),
            }

        if (isinstance(device, AgilentHPLCStandard)
            and not isinstance(device, AgilentHPLC2Pumps)):
            uv_status = {
                'uv_lamp_status'    : device.get_uv_lamp_power_status(),
                'vis_lamp_status'   : device.get_vis_lamp_power_status(update=False),
                }

        else:
            uv_status = {}

        val = {
            'pump_status'       : pump_status,
            'autosampler_status': autosampler_status,
            'uv_status'         : uv_status,
        }

        self._return_value((name, cmd, val), comm_name)

        logger.debug("%s very slow status: %s", name, val)

    def _get_valve_status(self, name, **kwargs):
        logger.debug("Getting %s valve status", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]

        valve_status = {
            'buffer1'   : device.get_valve_position('buffer1'),
            'purge1'    : device.get_valve_position('purge1'),
            }

        if isinstance(device, AgilentHPLC2Pumps):
            valve_status['buffer2'] = device.get_valve_position('buffer2')
            valve_status['purge2'] = device.get_valve_position('purge2')
            valve_status['selector'] = device.get_valve_position('selector')
            valve_status['outlet'] = device.get_valve_position('outlet')

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

    def _set_pump_seal_wash_settings(self, name, vals, flow_path, **kwargs):
        logger.debug("Setting %s flow path %s seal wash settings", name,
            flow_path)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]

        mode = vals['mode']
        single_duration = vals['single_duration']
        period = vals['period']
        period_duration = vals['period_duration']

        success = device.set_hplc_seal_wash_settings(flow_path, mode,
            single_duration, period, period_duration, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Set %s flow path %s seal wash settings: %s", name,
            flow_path, success)

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

    def _remove_buffer(self, name, position, flow_path, **kwargs):
        logger.debug("Removing %s flow path %s buffer %s", name, flow_path,
            position)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.remove_buffer(position, flow_path, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Removed %s flow path %s buffer %s", name, flow_path,
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

    def _set_pump_off(self, name, flow_path, **kwargs):
        logger.debug("Setting %s flow path %s pump off", name, flow_path)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_hplc_pump_off(flow_path, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Set %s flow path %s pump off: %s", name, flow_path,
            success)

    def _set_autosampler_on(self, name, **kwargs):
        logger.debug("Setting %s autosampler on", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_autosampler_on(**kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Set %s autosampler on: %s", name, success)

    def _set_autosampler_therm_on(self, name, **kwargs):
        logger.debug("Setting %s autosampler thermostat on", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_autosampler_thermostat_on(**kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Set %s autosampler thermostat on: %s", name, success)

    def _set_autosampler_therm_off(self, name, **kwargs):
        logger.debug("Setting %s autosampler thermostat off", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_autosampler_thermostat_off(**kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Set %s autosampler thermostat off: %s", name, success)

    def _set_autosampler_temp(self, name, val, **kwargs):
        logger.debug("Setting %s autosampler temperature set point to %s ",
            name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_autosampler_temperature_set_point(val, **kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Set %s autosampler temperature set point to %s ",
            name, val)

    def _set_mwd_on(self, name, **kwargs):
        logger.debug("Setting %s uv on", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_uv_on(**kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Set %s uv on: %s", name, success)

    def _set_uv_lamp_on(self, name, **kwargs):
        logger.debug("Setting %s uv lamp on", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_uv_lamp_on(**kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Set %s uv lamp on: %s", name, success)

    def _set_uv_lamp_off(self, name, **kwargs):
        logger.debug("Setting %s uv lamp off", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_uv_lamp_off(**kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Set %s uv lamp off: %s", name, success)

    def _set_vis_lamp_on(self, name, **kwargs):
        logger.debug("Setting %s vis lamp on", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_vis_lamp_on(**kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Set %s vis lamp on: %s", name, success)

    def _set_vis_lamp_off(self, name, **kwargs):
        logger.debug("Setting %s vis lamp off", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_vis_lamp_off(**kwargs)

        self._return_value((name, cmd, success), comm_name)

        logger.debug("Set %s vis lamp off: %s", name, success)

    def _submit_sample(self, name, sample_name, acq_method, sample_loc, inj_vol,
        flow_rate, flow_accel, total_elution_vol, high_pressure_lim, **kwargs):
        logger.debug("Submitting sample %s to %s", sample_name, name)

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
        self._inst_elapsed_runtime = ''
        self._inst_total_runtime = ''
        self._inst_run_queue_status = ''
        self._inst_err_status = ''
        self._inst_errs = ''
        self._inst_run_queue = []

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
        self._pump1_seal_wash_mode = ''
        self._pump1_seal_wash_single_duration = ''
        self._pump1_seal_wash_period = ''
        self._pump1_seal_wash_period_duration = ''
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
        self._pump2_seal_wash_mode = ''
        self._pump2_seal_wash_single_duration = ''
        self._pump2_seal_wash_period = ''
        self._pump2_seal_wash_period_duration = ''

        self._buffer1_info = {}
        self._buffer2_info = {}

        self._buffer1_valve = 0
        self._purge1_valve = 0
        self._buffer2_valve = 0
        self._purge2_valve = 0
        self._selector_valve = 0
        self._outlet_valve = 0

        self._sampler_thermostat_power = ''
        self._sampler_submitting = ''
        self._sampler_temp = ''
        self._sampler_setpoint = ''

        self._uv_lamp_status = ''
        self._uv_vis_lamp_status = ''
        self._uv_280_abs = ''
        self._uv_260_abs = ''

        super(HPLCPanel, self).__init__(parent, panel_id, settings,
            *args, **kwargs)

    def _create_layout(self):
        """Creates the layout for the panel."""

        inst_sizer = self._create_inst_ctrls()
        flow_sizer = self._create_flow_ctrls()
        sampler_sizer = self._create_sampler_ctrls()
        buffer_sizer = self._create_buffer_ctrls()

        as_uv_sizer = wx.BoxSizer(wx.HORIZONTAL)
        as_uv_sizer.Add(sampler_sizer)

        if self._device_type == 'AgilentHPLCStandard':
            uv_sizer = self._create_uv_ctrls()
            as_uv_sizer.Add(uv_sizer, flag=wx.LEFT, border=self._FromDIP(5))

        sub_sizer1 = wx.BoxSizer(wx.HORIZONTAL)
        sub_sizer1.Add(as_uv_sizer)
        sub_sizer1.Add(buffer_sizer, flag=wx.LEFT|wx.EXPAND,
            border=self._FromDIP(5), proportion=1)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(inst_sizer, flag=wx.EXPAND, proportion=1)
        top_sizer.Add(flow_sizer, flag=wx.EXPAND)
        top_sizer.Add(sub_sizer1, flag=wx.EXPAND, proportion=1)

        self.SetSizer(top_sizer)

        self.Layout()
        self.Refresh()

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
        self._inst_errs_ctrl = wx.TextCtrl(inst_box,
            size=self._FromDIP((-1, 40)),
            style=wx.TE_MULTILINE|wx.TE_READONLY|wx.TE_BESTWRAP)
        self._abort_current_run_btn = wx.Button(inst_box,
            label='Abort Current Run')
        self._inst_runtime_ctrl = wx.StaticText(inst_box,
            size=self._FromDIP((60,-1)), style=wx.ST_NO_AUTORESIZE)

        self._abort_current_run_btn.Bind(wx.EVT_BUTTON, self._on_abort_current_run)


        inst_sizer = wx.GridBagSizer(vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        inst_sizer.Add(wx.StaticText(inst_box, label='Connected:'),
            (0,0), flag=wx.ALIGN_CENTER_VERTICAL)
        inst_sizer.Add(self._inst_connected_ctrl, (0,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        inst_sizer.Add(wx.StaticText(inst_box, label='Status:'),
            (1,0), flag=wx.ALIGN_CENTER_VERTICAL)
        inst_sizer.Add(self._inst_status_ctrl, (1,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        inst_sizer.Add(wx.StaticText(inst_box, label='Runtime (min):'),
            (2,0), flag=wx.ALIGN_CENTER_VERTICAL)
        inst_sizer.Add(self._inst_runtime_ctrl, (2,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        inst_sizer.Add(wx.StaticText(inst_box, label='Run queue status:'),
            (3,0), flag=wx.ALIGN_TOP)
        inst_sizer.Add(self._inst_run_queue_status_ctrl, (3,1),
            flag=wx.ALIGN_TOP)
        inst_sizer.Add(self._abort_current_run_btn, (4,0), span=(1,2),
            flag=wx.ALIGN_TOP)


        err_status_sizer = wx.BoxSizer(wx.HORIZONTAL)
        err_status_sizer.Add(wx.StaticText(inst_box, label='Error:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        err_status_sizer.Add(self._inst_err_status_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT, border=self._FromDIP(5))

        err_sizer = wx.BoxSizer(wx.VERTICAL)
        err_sizer.Add(err_status_sizer, flag=wx.BOTTOM, border=self._FromDIP(5))
        err_sizer.Add(self._inst_errs_ctrl, flag=wx.EXPAND, proportion=1)

        status_sizer = wx.BoxSizer(wx.HORIZONTAL)
        status_sizer.Add(inst_sizer, flag=wx.RIGHT, border=self._FromDIP(5))
        status_sizer.Add(err_sizer, flag=wx.EXPAND, proportion=1)

        self._run_queue_ctrl = RunList(inst_box, size=self._FromDIP((-1, 40)),
            style=wx.LC_REPORT|wx.BORDER_SUNKEN)
        self._pause_run_queue_btn = wx.Button(inst_box, label='Pause Queue')
        self._resume_run_queue_btn = wx.Button(inst_box, label='Resume Queue')

        self._pause_run_queue_btn.Bind(wx.EVT_BUTTON, self._on_pause_run_queue)
        self._resume_run_queue_btn.Bind(wx.EVT_BUTTON, self._on_resume_run_queue)

        run_queue_btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        run_queue_btn_sizer.Add(self._pause_run_queue_btn)
        run_queue_btn_sizer.Add(self._resume_run_queue_btn, flag=wx.LEFT,
            border=self._FromDIP(5))

        run_queue_sizer = wx.BoxSizer(wx.VERTICAL)
        run_queue_sizer.Add(wx.StaticText(inst_box, label='Run Queue'))
        run_queue_sizer.Add(self._run_queue_ctrl, flag=wx.EXPAND|wx.TOP,
            border=self._FromDIP(5), proportion=1)
        run_queue_sizer.Add(run_queue_btn_sizer, flag=wx.TOP,
            border=self._FromDIP(5))


        top_sizer = wx.StaticBoxSizer(inst_box, wx.HORIZONTAL)
        top_sizer.Add(status_sizer, flag=wx.EXPAND|wx.ALL, proportion=3,
            border=self._FromDIP(5))
        top_sizer.Add(run_queue_sizer, flag=wx.TOP|wx.BOTTOM|wx.RIGHT|wx.EXPAND,
            proportion=2, border=self._FromDIP(5))

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
        pump1_status_sizer.Add(wx.StaticText(pump1_box, label='Purge vol. (mL):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(self._pump1_purge_vol_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(wx.StaticText(pump1_box, label='Equil.:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(self._pump1_eq_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(wx.StaticText(pump1_box, label='Eq. vol. (mL):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(self._pump1_eq_vol_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        pump1_status_sizer.Add(wx.StaticText(pump1_box, label='Flow (mL/min):'),
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
        self._pump1_off_btn = wx.Button(pump1_box, label='Pump Off')
        # self._pump1_seal_wash_btn = wx.Button(pump1_box, label='Set Seal Wash')

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
        self._pump1_off_btn.Bind(wx.EVT_BUTTON, self._on_pump_off)
        # self._pump1_seal_wash_btn.Bind(wx.EVT_BUTTON, self._on_pump_seal_wash)


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
        pump1_btn_sizer.Add(self._pump1_off_btn)
        # pump1_btn_sizer.Add(self._pump1_seal_wash_btn)


        pump1_sizer = wx.StaticBoxSizer(pump1_box, wx.VERTICAL)
        pump1_sizer.Add(pump1_status_sizer, flag=wx.ALL, border=self._FromDIP(5))
        pump1_sizer.Add(pump1_ctrl_sizer1, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))
        pump1_sizer.Add(pump1_btn_sizer, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))

        mid_sizer = wx.BoxSizer(wx.VERTICAL)

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
            pump2_status_sizer.Add(wx.StaticText(pump2_box, label='Purge vol. (mL):'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(self._pump2_purge_vol_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(wx.StaticText(pump2_box, label='Equil.:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(self._pump2_eq_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(wx.StaticText(pump2_box, label='Eq. vol. (mL):'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(self._pump2_eq_vol_ctrl,
                flag=wx.ALIGN_CENTER_VERTICAL)
            pump2_status_sizer.Add(wx.StaticText(pump2_box, label='Flow (mL/min):'),
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
            self._pump2_off_btn = wx.Button(pump2_box, label='Pump Off')
            # self._pump2_seal_wash_btn = wx.Button(pump2_box, label='Set Seal Wash')


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
            self._pump2_off_btn.Bind(wx.EVT_BUTTON, self._on_pump_off)
            # self._pump2_seal_wash_btn.Bind(wx.EVT_BUTTON, self._on_pump_seal_wash)


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
            pump2_btn_sizer.Add(self._pump2_off_btn)
            # pump2_btn_sizer.Add(self._pump2_seal_wash_btn)


            pump2_sizer = wx.StaticBoxSizer(pump2_box, wx.VERTICAL)
            pump2_sizer.Add(pump2_status_sizer, flag=wx.ALL, border=self._FromDIP(5))
            pump2_sizer.Add(pump2_ctrl_sizer1, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
                border=self._FromDIP(5))
            pump2_sizer.Add(pump2_btn_sizer, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
                border=self._FromDIP(5))


        valve_sizer = self._create_valve_ctrls(flow_box)

        mid_sizer.Add(valve_sizer, flag=wx.TOP, border=self._FromDIP(5))

        flow_sizer = wx.BoxSizer(wx.HORIZONTAL)

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
        self._sampler_setpoint_ctrl = wx.StaticText(sampler_box,
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
        sampler_sizer.Add(wx.StaticText(sampler_box, label='Setpoint (C):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        sampler_sizer.Add(self._sampler_setpoint_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)

        self._thermostat_setpoint_ctrl = wx.TextCtrl(sampler_box,
            size=self._FromDIP((60,-1)), style=wx.ST_NO_AUTORESIZE)
        self._thermostat_setpoint_btn = wx.Button(sampler_box, label='Set')
        self._thermostat_setpoint_btn.Bind(wx.EVT_BUTTON, self._on_set_thermostat)

        setpoint_sizer = wx.BoxSizer(wx.HORIZONTAL)
        setpoint_sizer.Add(wx.StaticText(sampler_box, label='Set temp.:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        setpoint_sizer.Add(self._thermostat_setpoint_ctrl,
            flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL, border=self._FromDIP(5))
        setpoint_sizer.Add(self._thermostat_setpoint_btn,
            flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL, border=self._FromDIP(5))

        self._submit_sample_btn = wx.Button(sampler_box, label='Submit Sample')
        self._stop_sample_submission_btn = wx.Button(sampler_box,
            label='Stop Sample Submission')
        self._sampler_power_on_btn = wx.Button(sampler_box,
            label='Autosampler On')
        self._thermostat_on_btn = wx.Button(sampler_box,
            label='Therm. On')
        self._thermostat_off_btn = wx.Button(sampler_box,
            label='Therm. Off')

        self._submit_sample_btn.Bind(wx.EVT_BUTTON, self._on_submit_sample)
        self._stop_sample_submission_btn.Bind(wx.EVT_BUTTON,
            self._on_stop_submission)
        self._sampler_power_on_btn.Bind(wx.EVT_BUTTON,
            self._on_sampler_power_on)
        self._thermostat_on_btn.Bind(wx.EVT_BUTTON,
            self._on_thermostat_on)
        self._thermostat_off_btn.Bind(wx.EVT_BUTTON,
            self._on_thermostat_off)

        sampler_btn_sizer = wx.GridBagSizer(vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        sampler_btn_sizer.Add(self._submit_sample_btn, (0,0), span=(1,2))
        sampler_btn_sizer.Add(self._stop_sample_submission_btn, (1,0),
            span=(1,2))
        sampler_btn_sizer.Add(self._thermostat_on_btn, (2,0))
        sampler_btn_sizer.Add(self._thermostat_off_btn, (2,1))
        sampler_btn_sizer.Add(self._sampler_power_on_btn, (3,0),
            span=(1,2))

        top_sizer = wx.StaticBoxSizer(sampler_box, wx.VERTICAL)
        top_sizer.Add(sampler_sizer, flag=wx.ALL|wx.EXPAND,
            border=self._FromDIP(5))
        top_sizer.Add(setpoint_sizer, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))
        top_sizer.Add(sampler_btn_sizer, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))

        return top_sizer

    def _create_uv_ctrls(self):
        uv_box = wx.StaticBox(self, label='MWD')

        self._uv_lamp_status_ctrl = wx.StaticText(uv_box,
            size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
        self._uv_vis_lamp_status_ctrl = wx.StaticText(uv_box,
            size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
        self._uv_280_abs_ctrl = wx.StaticText(uv_box,
            size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)
        self._uv_260_abs_ctrl = wx.StaticText(uv_box,
            size=self._FromDIP((40,-1)), style=wx.ST_NO_AUTORESIZE)

        uv_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        uv_sizer.Add(wx.StaticText(uv_box, label='UV Lamp:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        uv_sizer.Add(self._uv_lamp_status_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        uv_sizer.Add(wx.StaticText(uv_box, label='Vis Lamp:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        uv_sizer.Add(self._uv_vis_lamp_status_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        uv_sizer.Add(wx.StaticText(uv_box, label='280 nm abs (mAU):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        uv_sizer.Add(self._uv_280_abs_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        uv_sizer.Add(wx.StaticText(uv_box, label='260 nm abs (mAU):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        uv_sizer.Add(self._uv_260_abs_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)

        self._mwd_power_on_btn = wx.Button(uv_box, label='MWD On')
        self._uv_lamp_on_btn = wx.Button(uv_box, label='UV Lamp On')
        self._uv_lamp_off_btn = wx.Button(uv_box, label='UV Lamp Off')
        self._vis_lamp_on_btn = wx.Button(uv_box, label='Vis Lamp On')
        self._vis_lamp_off_btn = wx.Button(uv_box, label='Vis Lamp Off')

        self._mwd_power_on_btn.Bind(wx.EVT_BUTTON, self._on_mwd_power_on)
        self._uv_lamp_on_btn.Bind(wx.EVT_BUTTON, self._on_uv_lamp_on)
        self._uv_lamp_off_btn.Bind(wx.EVT_BUTTON, self._on_uv_lamp_off)
        self._vis_lamp_on_btn.Bind(wx.EVT_BUTTON, self._on_vis_lamp_on)
        self._vis_lamp_off_btn.Bind(wx.EVT_BUTTON, self._on_vis_lamp_off)

        uv_btn_sizer = wx.FlexGridSizer(cols=2, hgap=self._FromDIP(5),
            vgap=self._FromDIP(5))
        uv_btn_sizer.Add(self._uv_lamp_on_btn)
        uv_btn_sizer.Add(self._uv_lamp_off_btn)
        uv_btn_sizer.Add(self._vis_lamp_on_btn)
        uv_btn_sizer.Add(self._vis_lamp_off_btn)
        uv_btn_sizer.Add(self._mwd_power_on_btn)

        top_sizer = wx.StaticBoxSizer(uv_box, wx.VERTICAL)
        top_sizer.Add(uv_sizer, flag=wx.ALL|wx.EXPAND,
            border=self._FromDIP(5))
        top_sizer.Add(uv_btn_sizer, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))

        return top_sizer

    def _create_buffer_ctrls(self):
        buffer_box = wx.StaticBox(self, label='Buffers')

        buffer1_box = wx.StaticBox(self, label='Buffer 1')

        self._buffer1_list = utils.BufferList(buffer1_box,
            size=self._FromDIP((-1, 100)),style=wx.LC_REPORT|wx.BORDER_SUNKEN)

        self._add_edit_buffer1_btn = wx.Button(buffer1_box, label='Add/Edit Buffer')
        self._remove_buffer1_btn = wx.Button(buffer1_box, label='Remove Buffer')

        self._add_edit_buffer1_btn.Bind(wx.EVT_BUTTON, self._on_add_edit_buffer)
        self._remove_buffer1_btn.Bind(wx.EVT_BUTTON, self._on_remove_buffer)

        button1_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button1_sizer.Add(self._add_edit_buffer1_btn, flag=wx.RIGHT,
            border=self._FromDIP(5))
        button1_sizer.Add(self._remove_buffer1_btn)

        buffer1_sizer = wx.StaticBoxSizer(buffer1_box, wx.VERTICAL)
        buffer1_sizer.Add(self._buffer1_list, flag=wx.EXPAND|wx.ALL,
            proportion=1, border=self._FromDIP(5))
        buffer1_sizer.Add(button1_sizer, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))

        if self._device_type == 'AgilentHPLC2Pumps':
            buffer2_box = wx.StaticBox(self, label='Buffer 2')

            self._buffer2_list = utils.BufferList(buffer2_box,
                size=self._FromDIP((-1, 100)),style=wx.LC_REPORT|wx.BORDER_SUNKEN)

            self._add_edit_buffer2_btn = wx.Button(buffer2_box,
                label='Add/Edit Buffer')
            self._remove_buffer2_btn = wx.Button(buffer2_box,
                label='Remove Buffer')

            self._add_edit_buffer2_btn.Bind(wx.EVT_BUTTON,
                self._on_add_edit_buffer)
            self._remove_buffer2_btn.Bind(wx.EVT_BUTTON,
                self._on_remove_buffer)

            button2_sizer = wx.BoxSizer(wx.HORIZONTAL)
            button2_sizer.Add(self._add_edit_buffer2_btn, flag=wx.RIGHT,
                border=self._FromDIP(5))
            button2_sizer.Add(self._remove_buffer2_btn)

            buffer2_sizer = wx.StaticBoxSizer(buffer2_box, wx.VERTICAL)
            buffer2_sizer.Add(self._buffer2_list, flag=wx.EXPAND|wx.ALL,
                proportion=1, border=self._FromDIP(5))
            buffer2_sizer.Add(button2_sizer, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
                border=self._FromDIP(5))

        top_sizer = wx.StaticBoxSizer(buffer_box, wx.HORIZONTAL)
        top_sizer.Add(buffer1_sizer, flag=wx.EXPAND|wx.ALL, proportion=1,
            border=self._FromDIP(5))
        if self._device_type == 'AgilentHPLC2Pumps':
            top_sizer.Add(buffer2_sizer, flag=wx.EXPAND|wx.ALL, proportion=1,
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
            self._update_status_cmd(get_fast_hplc_status_cmd, 2)

            get_slow_hplc_status_cmd = ['get_slow_hplc_status', [self.name,], {}]
            self._update_status_cmd(get_slow_hplc_status_cmd, 35)

            get_very_slow_hplc_status_cmd = ['get_very_slow_hplc_status', [self.name,], {}]
            self._update_status_cmd(get_very_slow_hplc_status_cmd, 180)

            get_valve_status_cmd = ['get_valve_status', [self.name,], {}]
            self._update_status_cmd(get_valve_status_cmd, 15)

            methods_cmd = ['get_methods', [self.name,], {}]
            methods = self._send_cmd(methods_cmd, True)
            self._methods = methods

            sp_methods_cmd = ['get_sample_prep_methods', [self.name,], {}]
            sp_methods = self._send_cmd(sp_methods_cmd, True)
            self._sp_methods = sp_methods

        logger.info('Initialized HPLC %s on startup', self.name)

    def _on_error_collapse(self, evt):
        self.Layout()
        self.Refresh()
        self.SendSizeEvent()

    def _on_abort_current_run(self, evt):
        if len(self._inst_run_queue) > 0:
            cmd = ['abort_current_run', [self.name,], {}]
            self._send_cmd(cmd, False)

    def get_default_switch_flow_path_settings(self):
        default_switch_settings = {
            'purge_vol'                 : self.settings['switch_purge_volume'],
            'purge_rate'                : self.settings['switch_purge_rate'],
            'purge_accel'               : self.settings['switch_purge_accel'],
            'restore_flow_after_switch' : self.settings['restore_flow_after_switch'],
            'switch_with_sample'        : self.settings['switch_with_sample'],
            'stop_flow1'                : self.settings['switch_stop_flow1'],
            'stop_flow2'                : self.settings['switch_stop_flow2'],
            'purge_active'              : self.settings['switch_purge_active'],
            }

        return default_switch_settings

    def _on_set_flow_path(self, evt):
        evt_obj = evt.GetEventObject()

        if self._set_path1_btn == evt_obj:
            flow_path = 1
        elif self._set_path2_btn == evt_obj:
            flow_path = 2

        default_switch_settings = self.get_default_switch_flow_path_settings()

        switch_dialog = SwitchDialog(self, default_switch_settings,
            title='Switch active flowpath to {} settings'.format(flow_path))
        result = switch_dialog.ShowModal()

        if result == wx.ID_OK:
            switch_settings = switch_dialog.get_settings()
        else:
            switch_settings = None

        switch_dialog.Destroy()

        if switch_settings is not None:
            self._validate_and_switch(flow_path, switch_settings)

    def _validate_and_switch(self, flow_path, switch_settings, verbose=True):
        valid, errors = self.validate_switch_params(switch_settings)

        if valid:
            switch_settings['purge_max_pressure'] = self.settings['purge_max_pressure']

            cmd = ['set_active_flow_path', [self.name, flow_path], switch_settings]
            self._send_cmd(cmd, False)

            do_switch = True

        else:
            do_switch = False

            if verbose:
                msg = 'The following field(s) have invalid values:'
                for err in errors:
                    msg = msg + '\n- ' + err
                msg = msg + ('\n\nPlease correct these errors, then start the '
                    'switch.')

                wx.CallAfter(wx.MessageBox, msg, 'Error in switch parameters',
                    style=wx.OK|wx.ICON_ERROR)

        return do_switch

    def validate_switch_params(self, switch_settings):
        errors = []

        if switch_settings['purge_active']:
            try:
                switch_settings['purge_volume'] = float(switch_settings['purge_volume'])
            except Exception:
                errors.append('Purge volume (must be >0)')

            try:
                switch_settings['purge_rate'] = float(switch_settings['purge_rate'])
            except Exception:
                errors.append('Purge rate (must be >0)')

            try:
                switch_settings['purge_accel'] = float(switch_settings['purge_accel'])
            except Exception:
                errors.append('Purge acceleration (must be >0)')

        if switch_settings['purge_active']:
            if isinstance(switch_settings['purge_volume'], float):
                if switch_settings['purge_volume'] <= 0:
                    errors.append('Purge volume (must be >0)')

            if isinstance(switch_settings['purge_rate'], float):
                if switch_settings['purge_rate'] <= 0:
                    errors.append('Purge rate (must be >0)')

            if isinstance(switch_settings['purge_accel'], float):
                if switch_settings['purge_accel'] <= 0:
                    errors.append('Purge acceleration (must be >0)')

        if len(errors) > 0:
            valid = False
        else:
            valid =  True

        return valid, errors

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

        self._stop_flow(flow_path)

    def _stop_flow(self, flow_path):
        cmd = ['stop_pump{}'.format(flow_path), [self.name,], {}]
        self._send_cmd(cmd, False)

        if flow_path == 1:
            if '0.0' != self._pump1_flow_target:
                    wx.CallAfter(self._pump1_flow_target_ctrl.SetLabel, '0.0')
                    self._pump1_flow_target = '0.0'

        elif flow_path == 2:
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

        elif flow_path == 2:
            if '0.0' != self._pump2_flow_target:
                    wx.CallAfter(self._pump2_flow_target_ctrl.SetLabel, '0.0')
                    self._pump2_flow_target = '0.0'

    def _on_purge(self, evt):
        evt_obj = evt.GetEventObject()

        if self._pump1_purge_btn == evt_obj:
            flow_path = 1
        elif self._pump2_purge_btn == evt_obj:
            flow_path = 2

        default_purge_settings = {
            'purge_vol'     : self.settings['purge_volume'],
            'purge_rate'    : self.settings['purge_rate'],
            'purge_accel'   : self.settings['purge_accel'],
            'restore_flow_after_purge'  : self.settings['restore_flow_after_purge'],
            'purge_with_sample' : self.settings['purge_with_sample'],
            'stop_before_purge' : self.settings['stop_before_purge'],
            'stop_after_purge'  : self.settings['stop_after_purge'],
        }

        purge_dialog = PurgeDialog(self, default_purge_settings,
            title='Purge {} settings'.format(flow_path))
        result = purge_dialog.ShowModal()

        if result == wx.ID_OK:
            purge_settings = purge_dialog.get_settings()
        else:
            purge_settings = None

        purge_dialog.Destroy()

        if purge_settings is not None:
            purge_vol = purge_settings.pop('purge_vol')

            try:
                purge_vol = float(purge_vol)
                purge_settings['purge_rate'] = float(purge_settings['purge_rate'])
                purge_settings['purge_accel'] = float(purge_settings['purge_accel'])
                purge_settings['purge_max_pressure'] = self.settings['purge_max_pressure']
            except Exception:
                purge_vol = None

        else:
            purge_vol = None

        if purge_vol is not None:
            cmd = ['purge_flow_path', [self.name, flow_path, purge_vol],
                purge_settings]
            self._send_cmd(cmd, False)

    def _on_stop_purge(self, evt):
        evt_obj = evt.GetEventObject()

        if self._pump1_stop_purge_btn == evt_obj:
            flow_path = 1
        elif self._pump2_stop_purge_btn == evt_obj:
            flow_path = 2

        self._stop_purge(flow_path)

    def _stop_purge(self, flow_path):
        cmd = ['stop_purge', [self.name, flow_path,], {}]
        self._send_cmd(cmd, False)

    def get_default_equil_settings(self):
        default_equil_settings = {
            'equil_vol'     : self.settings['equil_volume'],
            'equil_rate'    : self.settings['equil_rate'],
            'equil_accel'   : self.settings['equil_accel'],
            'purge'         : self.settings['equil_purge'],
            'purge_vol'     : self.settings['purge_volume'],
            'purge_rate'    : self.settings['purge_rate'],
            'purge_accel'   : self.settings['purge_accel'],
            'equil_with_sample' : self.settings['equil_with_sample'],
            'stop_after_equil'  : self.settings['stop_after_equil'],
        }

        return default_equil_settings

    def _on_eq(self, evt):
        evt_obj = evt.GetEventObject()

        if self._pump1_eq_btn == evt_obj:
            flow_path = 1
        elif self._pump2_eq_btn == evt_obj:
            flow_path = 2

        default_equil_settings = self.get_default_equil_settings()

        equil_dialog = EquilDialog(self, default_equil_settings,
            title='Equilibration {} settings'.format(flow_path))
        result = equil_dialog.ShowModal()

        if result == wx.ID_OK:
            equil_settings = equil_dialog.get_settings()
        else:
            equil_settings = None

        equil_dialog.Destroy()

        if equil_settings is not None:
            self._validate_and_equilibrate(flow_path, equil_settings)

    def _validate_and_equilibrate(self, flow_path, equil_settings, verbose=True):
        valid, errors = self.validate_equil_params(equil_settings)

        if valid:
            equil_vol = equil_settings.pop('equil_vol')
            equil_rate = equil_settings.pop('equil_rate')
            equil_accel = equil_settings.pop('equil_accel')

            equil_settings['purge_max_pressure'] = self.settings['purge_max_pressure']

            cmd = ['equil_flow_path', [self.name, flow_path, equil_vol,
                equil_rate, equil_accel], equil_settings]
            self._send_cmd(cmd, False)

            run_equil = True

        else:
            run_equil = False

            if verbose:
                msg = 'The following field(s) have invalid values:'
                for err in errors:
                    msg = msg + '\n- ' + err
                msg = msg + ('\n\nPlease correct these errors, then start the '
                    'equilibration.')

                wx.CallAfter(wx.MessageBox, msg, 'Error in equilibration parameters',
                    style=wx.OK|wx.ICON_ERROR)

        return run_equil

    def validate_equil_params(self, equil_settings):
        equil_vol = equil_settings['equil_vol']
        equil_rate = equil_settings['equil_rate']
        equil_accel = equil_settings['equil_accel']

        errors = []

        try:
            equil_vol = float(equil_vol)
        except Exception:
            errors.append('Equilibration volume (must be >0)')

        try:
            equil_rate = float(equil_rate)
        except Exception:
            errors.append('Equilibration rate (must be >0)')

        try:
            equil_accel = float(equil_accel)
        except Exception:
            errors.append('Equilibration acceleration (must be >0)')

        if equil_settings['purge']:
            try:
                equil_settings['purge_volume'] = float(equil_settings['purge_volume'])
            except Exception:
                errors.append('Purge volume (must be >0)')

            try:
                equil_settings['purge_rate'] = float(equil_settings['purge_rate'])
            except Exception:
                errors.append('Purge rate (must be >0)')

            try:
                equil_settings['purge_accel'] = float(equil_settings['purge_accel'])
            except Exception:
                errors.append('Purge acceleration (must be >0)')

        if isinstance(equil_vol, float):
            if equil_vol <= 0:
                errors.append('Equilibration volume (must be >0)')

        if isinstance(equil_rate, float):
            if equil_rate <= 0:
                errors.append('Equilibration rate (must be >0)')

        if isinstance(equil_accel, float):
            if equil_accel <= 0:
                errors.append('Equilibration acceleration (must be >0)')

        if equil_settings['purge']:
            if isinstance(equil_settings['purge_volume'], float):
                if equil_settings['purge_volume'] <= 0:
                    errors.append('Purge volume (must be >0)')

            if isinstance(equil_settings['purge_rate'], float):
                if equil_settings['purge_rate'] <= 0:
                    errors.append('Purge rate (must be >0)')

            if isinstance(equil_settings['purge_accel'], float):
                if equil_settings['purge_accel'] <= 0:
                    errors.append('Purge acceleration (must be >0)')

        if len(errors) > 0:
            valid = False
        else:
            valid =  True

        if valid:
            equil_settings['equil_vol'] = equil_vol
            equil_settings['equil_rate'] = equil_rate
            equil_settings['equil_accel'] = equil_accel

        return valid, errors


    def _on_stop_eq(self, evt):
        evt_obj = evt.GetEventObject()

        if self._pump1_stop_eq_btn == evt_obj:
            flow_path = 1
        elif self._pump2_stop_eq_btn == evt_obj:
            flow_path = 2

        self._stop_eq(flow_path)

    def _stop_eq(self, flow_path):
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

    def _on_pump_off(self, evt):
        evt_obj = evt.GetEventObject()

        if self._pump1_off_btn == evt_obj:
            flow_path = 1
        elif self._pump2_off_btn == evt_obj:
            flow_path = 2

        cmd = ['set_pump_off', [self.name, flow_path], {}]
        self._send_cmd(cmd, False)

    def _on_pump_seal_wash(self, evt):
        evt_obj = evt.GetEventObject()

        if self._pump1_seal_wash_btn == evt_obj:
            flow_path = 1
        elif self._pump2_seal_wash_btn == evt_obj:
            flow_path = 2

        if flow_path == 1:
            current_wash_settings = {
                'mode'              : self._pump1_seal_wash_mode,
                'single_duration'   : self._pump1_seal_wash_single_duration,
                'period'            : self._pump1_seal_wash_period,
                'period_duration'   : self._pump1_seal_wash_period_duration,
                }
        elif flow_path == 2:
            current_wash_settings = {
                'mode'              : self._pump2_seal_wash_mode,
                'single_duration'   : self._pump2_seal_wash_single_duration,
                'period'            : self._pump2_seal_wash_period,
                'period_duration'   : self._pump2_seal_wash_period_duration,
                }

        # Need to redo from here
        wash_dialog = SealWashDialog(self, current_wash_settings,
            title='Pump {} seal wash settings'.format(flow_path))
        result = wash_dialog.ShowModal()

        if result == wx.ID_OK:
            wash_settings = wash_dialog.get_settings()
        else:
            wash_settings = None

        wash_dialog.Destroy()

        errors = []

        print(wash_settings)

        if wash_settings is not None:

            if wash_settings['mode'] == 'Single':
                if (wash_settings['single_duration'] == ''
                    or float(wash_settings['single_duration']) <= 0):
                    errors.append(('- For "Single" wash mode, must set single '
                        'duration to a value > 0'))
            elif wash_settings['mode'] == 'Periodic':
                if (wash_settings['period'] == ''
                    or float(wash_settings['period']) <= 0):
                    period = -1
                    errors.append(('- For "Periodic" wash mode, must set period '
                        'to a value > 0'))
                else:
                    period = float(wash_settings['period'])

                if (wash_settings['period_duration'] == ''
                    or float(wash_settings['period_duration']) <= 0):
                    period_duration = -1
                    errors.append(('- For "Periodic" wash mode, must set period '
                        'duration to a value > 0'))
                else:
                    period_duration = float(wash_settings['period_duration'])

                if period > 0 and period_duration > 0:
                    if period_duration > period:
                        errors.append(('- For "Periodic" wash mode, must set '
                            'period to be greater than period duration'))

            if len(errors) > 0:
                msg = ('Seal wash settings could not be set, the following '
                    'errors were found:\n')
                msg += '\n'.join(errors)
                error_dialog = wx.MessageDialog(self, msg, 'Error setting seal wash')
                error_dialog.ShowModal()
                error_dialog.Destroy()
                wash_settings = None

        if wash_settings is not None:
            cmd = ['set_seal_wash_settings', [self.name, wash_settings,
                flow_path], {}]
            self._send_cmd(cmd, False)

    def _on_set_valve_position(self, evt):
        evt_obj = evt.GetEventObject()

        if evt_obj == self._buffer1_valve_ctrl:
            valve = 'buffer1'
            val = self._buffer1_valve_ctrl.GetValue()
        elif evt_obj == self._purge1_valve_ctrl:
            valve = 'purge1'
            val = self._purge1_valve_ctrl.GetValue()
        elif evt_obj == self._buffer2_valve_ctrl:
            valve = 'buffer2'
            val = self._buffer2_valve_ctrl.GetValue()
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

    def _on_set_thermostat(self, evt):

        val = self._thermostat_setpoint_ctrl.GetValue()

        try:
            val = float(val)
        except ValueError:
            val = None

        if val is not None:
            cmd = ['set_autosampler_temp', [self.name, val], {}]
            self._send_cmd(cmd, False)

            if str(val) != self._sampler_setpoint:
                wx.CallAfter(self._sampler_setpoint_ctrl.SetLabel, str(val))
                self._sampler_setpoint = str(val)

    def get_default_sample_settings(self):


        default_sample_settings = {
            'acq_method'    : self.settings['acq_method'],
            'sample_loc'    : self.settings['sample_loc'],
            'inj_vol'       : self.settings['inj_vol'],
            'flow_rate'     : self.settings['flow_rate'],
            'flow_accel'    : self.settings['flow_accel'],
            'elution_vol'   : self.settings['elution_vol'],
            'pressure_lim'  : self.settings['sample_pressure_lim'],
            'result_path'   : self.settings['result_path'],
            'sp_method'     : self.settings['sp_method'],
            'wait_for_flow_ramp'    : self.settings['wait_for_flow_ramp'],
            'settle_time'   : self.settings['settle_time'],
            'all_acq_methods'       : self._methods,
            'all_sample_methods'    : self._sp_methods,
            }

        default_method = default_sample_settings['acq_method']
        if default_method not in default_sample_settings['all_acq_methods']:
            default_method = os.path.splitext(default_method)[0]+'.amx'

            if default_method not in default_sample_settings['all_acq_methods']:
                default_method ='.\\{}'.format(default_method)

            default_sample_settings['acq_method'] = default_method

        default_sp_method = default_sample_settings['sp_method']
        if default_sp_method not in default_sample_settings['all_sample_methods']:
            if default_sp_method != '':
                default_sp_method = os.path.splitext(default_sp_method)[0]+'.smx'

                if default_method not in default_sample_settings['all_sample_methods']:
                    default_method ='.\\{}'.format(default_method)
            else:
                default_sp_method = 'None'

            default_sample_settings['sp_method'] = default_sp_method

        return default_sample_settings

    def _on_submit_sample(self, evt):
        default_sample_settings = self.get_default_sample_settings()

        sample_dialog = SampleDialog(self, default_sample_settings,
            title='Sample submission settings')
        result = sample_dialog.ShowModal()

        if result == wx.ID_OK:
            sample_settings = sample_dialog.get_settings()
        else:
            sample_settings = None

        sample_dialog.Destroy()

        if sample_settings is not None:
            self._validate_and_submit_sample(sample_settings)

    def _validate_and_submit_sample(self, sample_settings, verbose=True):
        valid, errors = self.validate_injection_params(sample_settings)

        if valid:

            sample_name = sample_settings.pop('sample_name')
            acq_method = sample_settings.pop('acq_method')
            sample_loc = sample_settings.pop('sample_loc')
            inj_vol = sample_settings.pop('inj_vol')
            flow_rate = sample_settings.pop('flow_rate')
            flow_accel = sample_settings.pop('flow_accel')
            elution_vol = sample_settings.pop('elution_vol')
            pressure_lim = sample_settings.pop('pressure_lim')

            cmd = ['submit_sample', [self.name, sample_name, acq_method,
                sample_loc, inj_vol, flow_rate, flow_accel, elution_vol,
                pressure_lim], sample_settings]
            self._send_cmd(cmd, False)

            run_sample = True

        else:
            run_sample = False

            if verbose:
                msg = 'The following field(s) have invalid values:'
                for err in errors:
                    msg = msg + '\n- ' + err
                msg = msg + ('\n\nPlease correct these errors, then start the run.')

                wx.CallAfter(wx.MessageBox, msg, 'Error in injection parameters',
                    style=wx.OK|wx.ICON_ERROR)

        return run_sample

    def validate_injection_params(self, sample_settings):
        sample_name = sample_settings['sample_name']
        acq_method = sample_settings['acq_method']
        sample_loc = sample_settings['sample_loc']
        inj_vol = sample_settings['inj_vol']
        flow_rate = sample_settings['flow_rate']
        flow_accel = sample_settings['flow_accel']
        elution_vol = sample_settings['elution_vol']
        pressure_lim = sample_settings['pressure_lim']

        errors = []

        if len(sample_name) == 0:
            errors.append('Sample name (must not be blank)')

        if len(acq_method) == 0:
            errors.append('Acquisition method (must not be blank)')

        if '-' in sample_loc:
            drawer, spot = sample_loc.split('-')
            if drawer not in ['D1F', 'D1B', 'D2F', 'D2B', 'D3F', 'D3B']:
                errors.append('Drawer (not a valid drawer location)')

            if not spot[0].isalpha():
                errors.append('Sample well/position not valid')

            elif not spot[1:].isdigit():
                errors.append('Sample well/position not valid')

        else:
            if not sample_loc.isdigit():
                errors.append('Sample well/position not valid')

        try:
            inj_vol = float(inj_vol)
        except Exception:
            errors.append('Injection volume (between 1 and {} uL)'.format(
                self.settings['max_inj_vol']))

        try:
            flow_rate = float(flow_rate)
        except Exception:
            errors.append('Flow rate (must be >0)')

        try:
            flow_accel = float(flow_accel)
        except Exception:
            errors.append('Flow acceleration (must be >0)')

        try:
            elution_vol = float(elution_vol)
        except Exception:
            errors.append('Elution volume (must be >0)')

        try:
            pressure_lim = float(pressure_lim)
        except Exception:
            errors.append('Pressure limit (must be >0)')

        try:
            if sample_settings['wait_for_flow_ramp']:
                sample_settings['settle_time'] = float(sample_settings['settle_time'])
        except Exception:
            errors.append('Settling time (must be >=0)')

        if isinstance(inj_vol, float):
            if inj_vol < 1 or inj_vol > self.settings['max_inj_vol']:
                errors.append('Injection volume (between 1 and {} uL)'.format(
                    self.settings['max_inj_vol']))

        if isinstance(flow_rate, float):
            if flow_rate <= 0:
                errors.append('Flow rate (must be >0)')

        if isinstance(flow_accel, float):
            if flow_accel <= 0:
                errors.append('Flow acceleration (must be >0)')

        if isinstance(elution_vol, float):
            if elution_vol <= 0:
                errors.append('Elution volume (must be >0)')

        if isinstance(pressure_lim, float):
            if pressure_lim <= 0:
                errors.append('Pressure limit (must be >0)')

        if sample_settings['wait_for_flow_ramp']:
            if isinstance(sample_settings['settle_time'], float):
                if sample_settings['settle_time'] < 0:
                    errors.append('Settling time (must be >=0)')

        if len(errors) > 0:
            valid = False
        else:
            valid =  True

        if valid:
            sample_settings['inj_vol'] = inj_vol
            sample_settings['flow_rate'] = flow_rate
            sample_settings['flow_accel'] = flow_accel
            sample_settings['elution_vol'] = elution_vol
            sample_settings['pressure_lim'] = pressure_lim

        return valid, errors


    def _on_stop_submission(self, evt):
        cmd = ['stop_sample_submission', [self.name,], {}]
        self._send_cmd(cmd, False)

    def _on_thermostat_on(self, evt):
        cmd = ['set_autosampler_therm_on', [self.name,], {}]
        self._send_cmd(cmd, False)

    def _on_thermostat_off(self, evt):
        cmd = ['set_autosampler_therm_off', [self.name,], {}]
        self._send_cmd(cmd, False)

    def _on_sampler_power_on(self, evt):
        cmd = ['set_autosampler_on', [self.name,], {}]
        self._send_cmd(cmd, False)

    def _on_add_edit_buffer(self, evt):
        evt_obj = evt.GetEventObject()

        if self._add_edit_buffer1_btn == evt_obj:
            flow_path = 1
            buffer_info = self._buffer1_info
        elif self._add_edit_buffer2_btn == evt_obj:
            buffer_info = self._buffer2_info
            flow_path = 2

        buffer_entry_dlg = utils.BufferEntryDialog(self, buffer_info,
            title='Add/Edit pump {} buffer'.format(flow_path))
        result = buffer_entry_dlg.ShowModal()

        if result == wx.ID_OK:
            pos, vol, descrip = buffer_entry_dlg.get_settings()
        else:
            vol = None

        buffer_entry_dlg.Destroy()

        try:
            vol = float(vol)
        except Exception:
            vol = None

        if vol is not None:
            vol = vol*1000
            cmd = ['set_buffer_info', [self.name, pos, vol, descrip, flow_path],
                {}]
            self._send_cmd(cmd, False)

    def _on_remove_buffer(self, evt):
        evt_obj = evt.GetEventObject()

        if self._remove_buffer1_btn == evt_obj:
            flow_path = 1
            buffer_info = self._buffer1_info
        elif self._remove_buffer1_btn == evt_obj:
            buffer_info = self._buffer2_info
            flow_path = 2

        choices = ['{} - {}'.format(key, buffer_info[key]['descrip'])
            for key in buffer_info]
        choice_pos = [key for key in buffer_info]

        choice_dlg = wx.MultiChoiceDialog(self,
            'Select pump {} buffer(s) to remove'.format(flow_path),
            'Remove Buffer', choices)
        result = choice_dlg.ShowModal()

        if result == wx.ID_OK:
            sel_items = choice_dlg.GetSelections()
        else:
            sel_items = None

        choice_dlg.Destroy()

        if sel_items is not None:
            remove_pos = [choice_pos[i] for i in sel_items]

            for pos in remove_pos:
                cmd = ['remove_buffer', [self.name, pos, flow_path], {}]
                self._send_cmd(cmd, True)

                self._remove_buffer_from_list(flow_path, pos)

    def _on_pause_run_queue(self, evt):
        cmd = ['pause_run_queue', [self.name,], {}]
        self._send_cmd(cmd, False)

    def _on_resume_run_queue(self, evt):
        cmd = ['resume_run_queue', [self.name,], {}]
        self._send_cmd(cmd, False)

    def _on_mwd_power_on(self, evt):
        cmd = ['set_mwd_on', [self.name,], {}]
        self._send_cmd(cmd, False)

    def _on_uv_lamp_on(self, evt):
        cmd = ['set_uv_lamp_on', [self.name,], {}]
        self._send_cmd(cmd, False)

    def _on_uv_lamp_off(self, evt):
        cmd = ['set_uv_lamp_off', [self.name,], {}]
        self._send_cmd(cmd, False)

    def _on_vis_lamp_on(self, evt):
        cmd = ['set_vis_lamp_on', [self.name,], {}]
        self._send_cmd(cmd, False)

    def _on_vis_lamp_off(self, evt):
        cmd = ['set_vis_lamp_off', [self.name,], {}]
        self._send_cmd(cmd, False)

    def _set_status(self, cmd, val):
        if cmd == 'get_fast_hplc_status':
            inst_status = val['instrument_status']
            connected = str(inst_status['connected'])
            status = str(inst_status['status'])
            run_queue_status = str(inst_status['run_queue_status'])
            run_queue = inst_status['run_queue']

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

            if run_queue != self._inst_run_queue:
                self._update_run_queue(run_queue)
                self._inst_run_queue = run_queue


            pump_status = val['pump_status']
            pump1_purge = str(pump_status['purging_pump1'][0])
            pump1_purge_vol = str(round(float(pump_status['purging_pump1'][1]),3))
            pump1_eq = str(pump_status['equilibrate_pump1'][0])
            pump1_eq_vol = str(round(float(pump_status['equilibrate_pump1'][1]),3))
            pump1_flow = str(round(float(pump_status['flow1']),3))
            pump1_pressure = str(round(float(pump_status['pressure1']),3))
            buffers1 = pump_status['all_buffer_info1']

            if pump1_purge != self._pump1_purge:
                wx.CallAfter(self._pump1_purge_ctrl.SetLabel, pump1_purge)
                self._pump1_purge = pump1_purge

                if pump1_purge.lower() == 'true':
                    wx.CallAfter(self._pump1_purge_btn.Disable)
                    wx.CallAfter(self._pump1_eq_btn.Disable)
                else:
                    if pump1_eq.lower() == 'false':
                        wx.CallAfter(self._pump1_purge_btn.Enable)
                        wx.CallAfter(self._pump1_eq_btn.Enable)

            if pump1_purge.lower() == 'false':
                pump1_purge_vol = '0.0'

            if pump1_purge_vol != self._pump1_purge_vol:
                wx.CallAfter(self._pump1_purge_vol_ctrl.SetLabel, pump1_purge_vol)
                self._pump1_purge_vol = pump1_purge_vol

            if pump1_eq != self._pump1_eq:
                wx.CallAfter(self._pump1_eq_ctrl.SetLabel, pump1_eq)
                self._pump1_eq = pump1_eq

                if pump1_eq.lower() == 'true':
                    wx.CallAfter(self._pump1_eq_btn.Disable)
                    wx.CallAfter(self._pump1_purge_btn.Disable)
                else:
                    if pump1_purge.lower() == 'false':
                        wx.CallAfter(self._pump1_eq_btn.Enable)
                        wx.CallAfter(self._pump1_purge_btn.Enable)

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

            for key, value in buffers1.items():
                pos = key
                vol = value['vol']
                descrip = value['descrip']

                self._update_buffer_list(1, pos, vol, descrip)

            self._buffer1_info = buffers1

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
                buffers2 = pump_status['all_buffer_info2']

                if pump2_purge != self._pump2_purge:
                    wx.CallAfter(self._pump2_purge_ctrl.SetLabel, pump2_purge)
                    self._pump2_purge = pump2_purge

                    if pump2_purge.lower() == 'true':
                        wx.CallAfter(self._pump2_purge_btn.Disable)
                        wx.CallAfter(self._pump2_eq_btn.Disable)
                    else:
                        if pump2_eq.lower() == 'false':
                            wx.CallAfter(self._pump2_purge_btn.Enable)
                            wx.CallAfter(self._pump2_eq_btn.Enable)

                if pump2_purge.lower() == 'false':
                    pump2_purge_vol = '0.0'

                if pump2_purge_vol != self._pump2_purge_vol:
                    wx.CallAfter(self._pump2_purge_vol_ctrl.SetLabel, pump2_purge_vol)
                    self._pump2_purge_vol = pump2_purge_vol

                if pump2_eq != self._pump2_eq:
                    wx.CallAfter(self._pump2_eq_ctrl.SetLabel, pump2_eq)
                    self._pump2_eq = pump2_eq

                    if pump2_eq.lower() == 'true':
                        wx.CallAfter(self._pump2_eq_btn.Disable)
                        wx.CallAfter(self._pump2_purge_btn.Disable)
                    else:
                        if pump2_purge.lower() == 'false':
                            wx.CallAfter(self._pump2_eq_btn.Enable)
                            wx.CallAfter(self._pump2_purge_btn.Enable)

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

                for key, value in buffers2.items():
                    pos = key
                    vol = value['vol']
                    descrip = value['descrip']

                    self._update_buffer_list(2, pos, vol, descrip)

                self._buffer2_info = buffers2


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

            if self._device_type == 'AgilentHPLCStandard':
                uv_status = val['uv_status']
                uv_280_abs = uv_status['uv_280_abs']
                uv_260_abs = uv_status['uv_260_abs']

                if uv_280_abs is not None:
                    uv_280_abs = str(round(uv_280_abs, 2))

                if uv_260_abs is not None:
                    uv_260_abs = str(round(uv_260_abs, 2))

                if uv_280_abs != self._uv_280_abs:
                    wx.CallAfter(self._uv_280_abs_ctrl.SetLabel,
                        uv_280_abs)
                    self._uv_280_abs = uv_280_abs

                if uv_260_abs != self._uv_260_abs:
                    wx.CallAfter(self._uv_260_abs_ctrl.SetLabel,
                        uv_260_abs)
                    self._uv_260_abs = uv_260_abs


        elif cmd == 'get_slow_hplc_status':
            inst_status = val['instrument_status']
            elapsed_runtime = inst_status['elapsed_runtime']
            total_runtime = inst_status['total_runtime']
            status = str(inst_status['status'])

            if (status != 'Run' and status != 'Injecting'
                and status != 'PostRun' and status != 'PreRun'):
                total_runtime = '0.0'
                elapsed_runtime = '0.0'
            else:
                total_runtime = str(round(total_runtime,1))
                elapsed_runtime = str(round(elapsed_runtime, 1))

            if((elapsed_runtime != self._inst_elapsed_runtime) or
                (total_runtime != self._inst_total_runtime)):
                wx.CallAfter(self._inst_runtime_ctrl.SetLabel,
                    '{}/{}'.format(elapsed_runtime, total_runtime))
                self._inst_elapsed_runtime =  elapsed_runtime
                self._inst_total_runtime =  total_runtime

            pump_status = val['pump_status']
            pump1_flow_target = str(round(float(pump_status['target_flow1']),3))
            pump1_flow_accel = str(round(float(pump_status['flow_accel1']),3))

            if pump1_flow_target != self._pump1_flow_target:
                wx.CallAfter(self._pump1_flow_target_ctrl.SetLabel,
                    pump1_flow_target)
                self._pump1_flow_target = pump1_flow_target

            if pump1_flow_accel != self._pump1_flow_accel:
                wx.CallAfter(self._pump1_flow_accel_ctrl.SetLabel,
                    pump1_flow_accel)
                self._pump1_flow_accel = pump1_flow_accel

            if self._device_type == 'AgilentHPLC2Pumps':
                pump2_flow_target = str(round(float(pump_status['target_flow2']),3))
                pump2_flow_accel = str(round(float(pump_status['flow_accel2']),3))

                if pump2_flow_target != self._pump2_flow_target:
                    wx.CallAfter(self._pump2_flow_target_ctrl.SetLabel,
                        pump2_flow_target)
                    self._pump2_flow_target = pump2_flow_target

                if pump2_flow_accel != self._pump2_flow_accel:
                    wx.CallAfter(self._pump2_flow_accel_ctrl.SetLabel,
                        pump2_flow_accel)
                    self._pump2_flow_accel = pump2_flow_accel


        elif cmd == 'get_very_slow_hplc_status':
            pump_status = val['pump_status']
            pump1_power = str(pump_status['power_status1'])
            pump1_pressure_lim = str(round(float(pump_status['high_pressure_lim1']),3))

            if pump1_power != self._pump1_power:
                wx.CallAfter(self._pump1_power_ctrl.SetLabel, pump1_power)
                self._pump1_power = pump1_power

            if pump1_pressure_lim != self._pump1_pressure_lim:
                wx.CallAfter(self._pump1_pressure_lim_ctrl.SetLabel, pump1_pressure_lim)
                self._pump1_pressure_lim = pump1_pressure_lim

            if self._device_type == 'AgilentHPLC2Pumps':
                pump2_power = str(pump_status['power_status2'])
                pump2_pressure_lim = str(round(float(pump_status['high_pressure_lim2']),3))
                # pump1_seal_wash = pump_status['seal_wash1']
                # pump2_seal_wash = pump_status['seal_wash2']

                if pump2_power != self._pump2_power:
                    wx.CallAfter(self._pump2_power_ctrl.SetLabel, pump2_power)
                    self._pump2_power = pump2_power

                if pump2_pressure_lim != self._pump2_pressure_lim:
                    wx.CallAfter(self._pump2_pressure_lim_ctrl.SetLabel,
                        pump2_pressure_lim)
                    self._pump2_pressure_lim = pump2_pressure_lim

                # self._pump1_seal_wash_mode = str(pump1_seal_wash['mode'])
                # self._pump1_seal_wash_single_duration = str(pump1_seal_wash['single_duration'])
                # self._pump1_seal_wash_period = str(pump1_seal_wash['period'])
                # self._pump1_seal_wash_period_duration = str(pump1_seal_wash['period_duration'])

                # self._pump2_seal_wash_mode = str(pump2_seal_wash['mode'])
                # self._pump2_seal_wash_single_duration = str(pump2_seal_wash['single_duration'])
                # self._pump2_seal_wash_period = str(pump2_seal_wash['period'])
                # self._pump2_seal_wash_period_duration = str(pump2_seal_wash['period_duration'])


            sampler_status = val['autosampler_status']
            thermostat_power = str(sampler_status['thermostat_power_status'])
            temperature_setpoint = str(round(float(sampler_status['temperature_setpoint']),3))

            if thermostat_power != self._sampler_thermostat_power:
                wx.CallAfter(self._sampler_thermostat_power_ctrl.SetLabel,
                    thermostat_power)
                self._sampler_thermostat_power = thermostat_power

            if temperature_setpoint != self._sampler_setpoint:
                wx.CallAfter(self._sampler_setpoint_ctrl.SetLabel,
                    temperature_setpoint)
                self._sampler_setpoint = temperature_setpoint

            if self._device_type == 'AgilentHPLCStandard':
                uv_status = val['uv_status']
                uv_lamp_status = uv_status['uv_lamp_status']
                vis_lamp_status = uv_status['vis_lamp_status']

                if uv_lamp_status != self._uv_lamp_status:
                    wx.CallAfter(self._uv_lamp_status_ctrl.SetLabel,
                        uv_lamp_status)
                    self._uv_lamp_status = uv_lamp_status

                if vis_lamp_status != self._uv_vis_lamp_status:
                    wx.CallAfter(self._uv_vis_lamp_status_ctrl.SetLabel,
                        vis_lamp_status)
                    self._uv_vis_lamp_status = vis_lamp_status


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

    def _update_buffer_list(self, flow_path, pos, vol, descrip):
        if flow_path == 1:
            buffer_list = self._buffer1_list
            buffer_info = self._buffer1_info
        elif flow_path == 2:
            buffer_list = self._buffer2_list
            buffer_info = self._buffer2_info

        vol = round(vol,1)

        update = True
        new_item = False
        if pos in buffer_info:
            cur_vol = buffer_info[pos]['vol']
            cur_descrip = buffer_info[pos]['descrip']

            if round(cur_vol,1) == vol and cur_descrip == descrip:
                update = False

        else:
            new_item = True

        vol = round(vol/1000., 4)

        if update:
            new_insert_pos = -1

            for i in range(buffer_list.GetItemCount()):
                item = buffer_list.GetItem(i)
                item_pos = buffer_list.GetItemData(i)

                if new_item and item_pos > int(pos):
                    new_insert_pos = i
                    break

                elif not new_item and item_pos == int(pos):
                    modif_pos = i
                    break

            if new_item:
                if new_insert_pos == -1:
                    new_insert_pos = buffer_list.GetItemCount()

                buffer_list.InsertItem(new_insert_pos, str(pos))
                buffer_list.SetItem(new_insert_pos, 1, str(vol))
                buffer_list.SetItem(new_insert_pos, 2, descrip)
                buffer_list.SetItemData(new_insert_pos, int(pos))
            else:
                buffer_list.SetItem(modif_pos, 1, str(vol))
                buffer_list.SetItem(modif_pos, 2, descrip)

    def _remove_buffer_from_list(self, flow_path, pos):
        if flow_path == 1:
            buffer_list = self._buffer1_list
        elif flow_path == 2:
            buffer_list = self._buffer2_list

        for i in range(buffer_list.GetItemCount()):
            item = buffer_list.GetItem(i)
            item_pos = buffer_list.GetItemData(i)

            if item_pos == int(pos):
                buffer_list.DeleteItem(i)
                break

    def _update_run_queue(self, run_queue):
        self._run_queue_ctrl.Freeze()

        while self._run_queue_ctrl.GetItemCount() > len(run_queue):
            self._run_queue_ctrl.DeleteItem(self._run_queue_ctrl.GetItemCount()-1)


        for i, run_data in enumerate(run_queue):
            if i < self._run_queue_ctrl.GetItemCount():
                self._run_queue_ctrl.SetItem(i, 0, run_data[1])
                self._run_queue_ctrl.SetItem(i, 1, run_data[0])
            else:
                self._run_queue_ctrl.InsertItem(i, run_data[1])
                self._run_queue_ctrl.SetItem(i, 1, run_data[0])

        self._run_queue_ctrl.Thaw()

    def _get_automator_state(self, flow_path):
        if self._flow_path_status.lower() == 'true':
            state = 'switch'

        elif flow_path == 1:
            if self._pump1_purge.lower() == 'true':
                state = 'equil'
            elif self._pump1_eq.lower() == 'true':
                state = 'equil'
            else:
                state = 'idle'

        elif flow_path == 2:
            if self._pump2_purge.lower() == 'true':
                state = 'equil'
            elif self._pump2_eq.lower() == 'true':
                state = 'equil'
            else:
                state = 'idle'

        return state

    def automator_callback(self, cmd_name, cmd_args, cmd_kwargs):
        # if cmd_name != 'status':
        #     print('automator_callback')
        #     print(cmd_name)
        #     print(cmd_args)
        #     print(cmd_kwargs)

        success = True

        if cmd_name == 'status':
            if (self._inst_status == 'Offline' or self._inst_status == 'Unknown'
                or self._inst_status == 'Error' or self._inst_status == 'Idle' or
                self._inst_status == 'NotReady' or self._inst_status == 'Standby'):

                # if self._sampler_submitting.lower() == 'true':
                #     state = 'run'
                # This seems to cause issues because the instrument status goes
                #back to idle before it goes to prerun, so automator triggers waits it shouldn't

                # else:
                inst_name = cmd_kwargs['inst_name']

                flow_path = int(inst_name.split('_')[-1].lstrip('pump'))

                state = self._get_automator_state(flow_path)

            elif (self._inst_status == 'Run' or self._inst_status == 'Injecting'
                or self._inst_status == 'PostRun' or self._inst_status == 'PreRun'):
                inst_name = cmd_kwargs['inst_name']
                flow_path = int(inst_name.split('_')[-1].lstrip('pump'))
                if self._device_type == 'AgilentHPLC2Pumps':
                    if flow_path == int(self._flow_path):
                        state = 'run'
                    else:
                        state = self._get_automator_state(flow_path)
                else:
                    state = 'run'

            else:
                state = 'idle'


        elif cmd_name == 'inject':
            acq_method = cmd_kwargs['acq_method']
            sp_method = cmd_kwargs['sp_method']

            if acq_method not in self._methods:
                acq_method = os.path.splitext(acq_method)[0]+'.amx'

                if acq_method not in self._methods:
                    acq_method = '.\\{}'.format(acq_method)

            if (sp_method is not None and sp_method != ''
                and sp_method not in self._sp_methods):
                sp_method = os.path.splitext(sp_method)[0]+'.smx'

                if sp_method not in self._sp_methods:
                    sp_method = '.\\{}'.format(sp_method)

            elif sp_method == '':
                sp_method = None

            cmd_kwargs['acq_method'] = acq_method
            cmd_kwargs['sp_method'] = sp_method

            if (acq_method in self._methods and (sp_method is None
                or sp_method in self._sp_methods)):
                success = self._validate_and_submit_sample(cmd_kwargs, False)

            else:
                success = False

            state = 'run'

        elif cmd_name == 'equilibrate':
            flow_path = cmd_kwargs.pop('flow_path')
            success = self._validate_and_equilibrate(flow_path, cmd_kwargs, False)

            state = 'equil'

        elif cmd_name == 'switch_pumps':
            flow_path = int(cmd_kwargs.pop('flow_path'))

            if flow_path != int(self._flow_path):
                success = self._validate_and_switch(flow_path, cmd_kwargs, False)
                state = 'switch'
            else:
                state = self._get_automator_state(flow_path)

        elif cmd_name == 'stop_flow':
            flow_path = cmd_kwargs['flow_path']
            self._stop_flow(flow_path)

            state = 'idle'

        elif cmd_name == 'abort':
            state = 'idle'
            inst_name = cmd_kwargs['inst_name']
            abort_flow_path = int(inst_name.split('_')[-1].lstrip('pump'))

            if ((self._inst_status == 'Run' or self._inst_status == 'Injecting'
                or self._inst_status == 'PostRun' or self._inst_status == 'PreRun')
                and abort_flow_path == int(self._flow_path)):
                self._on_abort_current_run(None)

            else:
                if self._flow_path_status.lower() == 'true':
                    self._on_stop_switch(None)

                elif abort_flow_path == 1:
                    if self._pump1_eq.lower() == 'true':
                        self._stop_eq(1)

                    elif (self._pump1_eq.lower() != 'true'
                        and self._pump1_purge.lower() == 'true'):
                        self._stop_purge(1)

                    elif (self._sampler_submitting.lower() == 'true'
                        and int(self._flow_path) == 1):
                        self._on_stop_submission(None)

                elif abort_flow_path == 2:
                    if self._pump2_eq.lower() == 'true':
                        self._stop_eq(2)

                    elif (self._pump2_eq.lower() != 'true'
                        and self._pump2_purge.lower() == 'true'):
                        self._stop_purge(2)

                    elif (self._sampler_submitting.lower() == 'true'
                        and int(self._flow_path) == 2):
                        self._on_stop_submission(None)

        elif cmd_name == 'full_status':
            if self._flow_path_status.lower() == 'true':
                state = 'Switching'
            elif self._sampler_submitting.lower() == 'true':
                state = 'Submitting'
            else:
                state = copy.copy(self._inst_status)

            runtime = float(self._inst_total_runtime)-float(self._inst_elapsed_runtime)
            rountime = round(runtime, 1)

            if self._pump1_purge.lower() == 'true':
                pump1_state = 'Equilibrating'
            elif self._pump1_eq.lower() == 'true':
                pump1_state = 'Equilibrating'
            elif float(self._pump1_flow) > 0:
                pump1_state = 'Flowing'
            else:
                pump1_state = 'Stopped'

            if self._device_type == 'AgilentHPLC2Pumps':
                if self._pump2_purge.lower() == 'true':
                    pump2_state = 'Equilibrating'
                elif self._pump2_eq.lower() == 'true':
                    pump2_state = 'Equilibrating'
                elif float(self._pump2_flow) > 0:
                    pump2_state = 'Flowing'
                else:
                    pump2_state = 'Stopped'
            else:
                pump2_state = ''

            state = {
                'state'         : state,
                'flow_path'     : copy.copy(self._flow_path),
                'runtime'       : str(runtime),
                'pump1_state'   : pump1_state,
                'pump1_fr'      : copy.copy(self._pump1_flow),
                'pump1_pressure': copy.copy(self._pump1_pressure),
                'pump2_state'   : pump2_state,
                'pump2_fr'      : copy.copy(self._pump2_flow),
                'pump2_pressure': copy.copy(self._pump2_pressure),
            }

        return state, success

    def on_exit(self):
        pass




class PurgeDialog(wx.Dialog):
    """
    Allows addition/editing of the buffer info in the buffer list
    """
    def __init__(self, parent, settings, *args, **kwargs):
        wx.Dialog.__init__(self, parent, *args,
            style=wx.RESIZE_BORDER|wx.CAPTION|wx.CLOSE_BOX, **kwargs)

        self.SetSize(self._FromDIP((400, 250)))

        self._create_layout(settings)

        self.CenterOnParent()

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _create_layout(self, settings):
        parent = self

        self._purge_vol_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))
        self._purge_rate_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))
        self._purge_accel_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))

        self._purge_rate_ctrl.SetValue(str(settings['purge_rate']))
        self._purge_vol_ctrl.SetValue(str(settings['purge_vol']))
        self._purge_accel_ctrl.SetValue(str(settings['purge_accel']))

        purge_sizer1 = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        purge_sizer1.Add(wx.StaticText(parent, label='Purge volume (mL):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        purge_sizer1.Add(self._purge_vol_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        purge_sizer1.Add(wx.StaticText(parent, label='Purge rate (mL/min):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        purge_sizer1.Add(self._purge_rate_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        purge_sizer1.Add(wx.StaticText(parent, label='Purge acceleration (mL/min^2):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        purge_sizer1.Add(self._purge_accel_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)

        self._purge_restore_flow = wx.CheckBox(parent,
            label='Restore flow to current rate after purge')
        self._purge_with_sample = wx.CheckBox(parent,
            label='Purge even if a sample is running')
        self._purge_stop_before = wx.CheckBox(parent,
            label='Ramp flow to 0 before switching purge valve at start')
        self._purge_stop_after = wx.CheckBox(parent,
            label='Ramp flow to 0 before switching purge valve at end')

        self._purge_restore_flow.SetValue(settings['restore_flow_after_purge'])
        self._purge_with_sample.SetValue(settings['purge_with_sample'])
        self._purge_stop_before.SetValue(settings['stop_before_purge'])
        self._purge_stop_after.SetValue(settings['stop_after_purge'])

        purge_sizer2 = wx.BoxSizer(wx.VERTICAL)
        purge_sizer2.Add(self._purge_restore_flow, flag=wx.BOTTOM,
            border=self._FromDIP(5))
        purge_sizer2.Add(self._purge_with_sample, flag=wx.BOTTOM,
            border=self._FromDIP(5))
        purge_sizer2.Add(self._purge_stop_before, flag=wx.BOTTOM,
            border=self._FromDIP(5))
        purge_sizer2.Add(self._purge_stop_after, flag=wx.BOTTOM,
            border=self._FromDIP(5))

        button_sizer = self.CreateButtonSizer(wx.OK | wx.CANCEL)

        top_sizer=wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(purge_sizer1, flag=wx.ALL, border=self._FromDIP(5))
        top_sizer.Add(purge_sizer2, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))
        top_sizer.Add(button_sizer ,flag=wx.BOTTOM|wx.RIGHT|wx.LEFT|wx.ALIGN_RIGHT,
            border=self._FromDIP(10))

        self.SetSizer(top_sizer)

    def get_settings(self):
        rate = self._purge_rate_ctrl.GetValue()
        vol = self._purge_vol_ctrl.GetValue()
        accel = self._purge_accel_ctrl.GetValue()
        restore_flow_after_purge = self._purge_restore_flow.GetValue()
        purge_with_sample = self._purge_with_sample.GetValue()
        stop_before_purge = self._purge_stop_before.GetValue()
        stop_after_purge = self._purge_stop_after.GetValue()

        settings = {
            'purge_rate'                : rate,
            'purge_vol'                 : vol,
            'purge_accel'               : accel,
            'restore_flow_after_purge'  : restore_flow_after_purge,
            'purge_with_sample'         : purge_with_sample,
            'stop_before_purge'         : stop_before_purge,
            'stop_after_purge'          : stop_after_purge,
            }

        return settings

class EquilDialog(wx.Dialog):
    """
    Allows addition/editing of the buffer info in the buffer list
    """
    def __init__(self, parent, settings, *args, **kwargs):
        wx.Dialog.__init__(self, parent, *args,
            style=wx.RESIZE_BORDER|wx.CAPTION|wx.CLOSE_BOX, **kwargs)

        self.SetSize(self._FromDIP((325, 325)))

        self._create_layout(settings)

        self.CenterOnParent()

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _create_layout(self, settings):
        parent = self

        self._equil_vol_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))
        self._equil_rate_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))
        self._equil_accel_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))

        self._equil_rate_ctrl.SetValue(str(settings['equil_rate']))
        self._equil_vol_ctrl.SetValue(str(settings['equil_vol']))
        self._equil_accel_ctrl.SetValue(str(settings['equil_accel']))

        equil_sizer1 = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        equil_sizer1.Add(wx.StaticText(parent, label='Equilibration volume (mL):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer1.Add(self._equil_vol_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer1.Add(wx.StaticText(parent, label='Equilibration rate (mL/min):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer1.Add(self._equil_rate_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer1.Add(wx.StaticText(parent, label='Equilibration acceleration (mL/min^2):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer1.Add(self._equil_accel_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)


        self._stop_after_equil = wx.CheckBox(parent,
            label='Stop flow after equilibration')
        self._stop_after_equil.SetValue(settings['stop_after_equil'])


        self._purge = wx.CheckBox(parent, label='Run purge')
        self._purge.SetValue(settings['purge'])

        self._purge_vol_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))
        self._purge_rate_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))
        self._purge_accel_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))

        self._purge_rate_ctrl.SetValue(str(settings['purge_rate']))
        self._purge_vol_ctrl.SetValue(str(settings['purge_vol']))
        self._purge_accel_ctrl.SetValue(str(settings['purge_accel']))

        purge_sizer1 = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        purge_sizer1.Add(wx.StaticText(parent, label='Purge volume (mL):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        purge_sizer1.Add(self._purge_vol_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        purge_sizer1.Add(wx.StaticText(parent, label='Purge rate (mL/min):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        purge_sizer1.Add(self._purge_rate_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        purge_sizer1.Add(wx.StaticText(parent, label='Purge acceleration (mL/min^2):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        purge_sizer1.Add(self._purge_accel_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)

        self._equil_with_sample = wx.CheckBox(parent,
            label='Equilibrate even if a sample is running')
        self._equil_with_sample.SetValue(settings['equil_with_sample'])


        button_sizer = self.CreateButtonSizer(wx.OK | wx.CANCEL)

        top_sizer=wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(equil_sizer1, flag=wx.ALL, border=self._FromDIP(5))
        top_sizer.Add(self._stop_after_equil, flag=wx.BOTTOM|wx.RIGHT|wx.LEFT,
            border=self._FromDIP(5))
        top_sizer.Add(self._purge, flag=wx.BOTTOM|wx.RIGHT|wx.LEFT,
            border=self._FromDIP(5))
        top_sizer.Add(purge_sizer1, flag=wx.BOTTOM|wx.RIGHT|wx.LEFT,
            border=self._FromDIP(5))
        top_sizer.Add(self._equil_with_sample, flag=wx.BOTTOM|wx.RIGHT|wx.LEFT,
            border=self._FromDIP(5))
        top_sizer.Add(button_sizer ,flag=wx.BOTTOM|wx.RIGHT|wx.LEFT|wx.ALIGN_RIGHT,
            border=self._FromDIP(10))

        self.SetSizer(top_sizer)

    def get_settings(self):
        equil_rate = self._equil_rate_ctrl.GetValue()
        equil_vol = self._equil_vol_ctrl.GetValue()
        equil_accel = self._equil_accel_ctrl.GetValue()
        purge = self._purge.GetValue()
        purge_rate = self._purge_rate_ctrl.GetValue()
        purge_volume = self._purge_vol_ctrl.GetValue()
        purge_accel = self._purge_accel_ctrl.GetValue()
        equil_with_sample = self._equil_with_sample.GetValue()
        stop_after_equil = self._stop_after_equil.GetValue()

        settings = {
            'equil_rate'        : equil_rate,
            'equil_vol'         : equil_vol,
            'equil_accel'       : equil_accel,
            'purge'             : purge,
            'purge_rate'        : purge_rate,
            'purge_volume'      : purge_volume,
            'purge_accel'       : purge_accel,
            'equil_with_sample' : equil_with_sample,
            'stop_after_equil'  : stop_after_equil,
            }

        return settings

class SwitchDialog(wx.Dialog):
    """
    Allows addition/editing of the buffer info in the buffer list
    """
    def __init__(self, parent, settings, *args, **kwargs):
        wx.Dialog.__init__(self, parent, *args,
            style=wx.RESIZE_BORDER|wx.CAPTION|wx.CLOSE_BOX, **kwargs)

        self.SetSize(self._FromDIP((300, 275)))

        self._create_layout(settings)

        self.CenterOnParent()

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _create_layout(self, settings):
        parent = self

        self._switch_restore_flow = wx.CheckBox(parent,
            label='Restore flow to current rate after switching')
        self._switch_with_sample = wx.CheckBox(parent,
            label='Switch even if a sample is running')
        self._switch_stop1 = wx.CheckBox(parent,
            label='Ramp pump 1 flow to 0 before switching')
        self._switch_stop2 = wx.CheckBox(parent,
            label='Ramp pump 2 flow to 0 before switching')
        self._purge_active = wx.CheckBox(parent,
            label='Purge active flow path after switching')

        self._switch_restore_flow.SetValue(settings['restore_flow_after_switch'])
        self._switch_with_sample.SetValue(settings['switch_with_sample'])
        self._switch_stop1.SetValue(settings['stop_flow1'])
        self._switch_stop2.SetValue(settings['stop_flow2'])
        self._purge_active.SetValue(settings['purge_active'])

        switch_sizer = wx.BoxSizer(wx.VERTICAL)
        switch_sizer.Add(self._switch_restore_flow, flag=wx.BOTTOM,
            border=self._FromDIP(5))
        switch_sizer.Add(self._switch_with_sample, flag=wx.BOTTOM,
            border=self._FromDIP(5))
        switch_sizer.Add(self._switch_stop1, flag=wx.BOTTOM,
            border=self._FromDIP(5))
        switch_sizer.Add(self._switch_stop2, flag=wx.BOTTOM,
            border=self._FromDIP(5))
        switch_sizer.Add(self._purge_active, flag=wx.BOTTOM,
            border=self._FromDIP(5))

        self._purge_vol_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))
        self._purge_rate_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))
        self._purge_accel_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))

        self._purge_rate_ctrl.SetValue(str(settings['purge_rate']))
        self._purge_vol_ctrl.SetValue(str(settings['purge_vol']))
        self._purge_accel_ctrl.SetValue(str(settings['purge_accel']))

        purge_sizer1 = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        purge_sizer1.Add(wx.StaticText(parent, label='Purge volume (mL):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        purge_sizer1.Add(self._purge_vol_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        purge_sizer1.Add(wx.StaticText(parent, label='Purge rate (mL/min):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        purge_sizer1.Add(self._purge_rate_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        purge_sizer1.Add(wx.StaticText(parent, label='Purge acceleration (mL/min^2):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        purge_sizer1.Add(self._purge_accel_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)

        button_sizer = self.CreateButtonSizer(wx.OK | wx.CANCEL)

        top_sizer=wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(switch_sizer, flag=wx.ALL, border=self._FromDIP(5))
        top_sizer.Add(purge_sizer1, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))
        top_sizer.Add(button_sizer ,flag=wx.BOTTOM|wx.RIGHT|wx.LEFT|wx.ALIGN_RIGHT,
            border=self._FromDIP(10))

        self.SetSizer(top_sizer)

    def get_settings(self):
        rate = self._purge_rate_ctrl.GetValue()
        vol = self._purge_vol_ctrl.GetValue()
        accel = self._purge_accel_ctrl.GetValue()
        restore_flow_after_switch = self._switch_restore_flow.GetValue()
        switch_with_sample = self._switch_with_sample.GetValue()
        stop_flow1 = self._switch_stop1.GetValue()
        stop_flow2 = self._switch_stop2.GetValue()
        purge_active = self._purge_active.GetValue()

        settings = {
            'purge_rate'                : rate,
            'purge_volume'              : vol,
            'purge_accel'               : accel,
            'restore_flow_after_switch' : restore_flow_after_switch,
            'switch_with_sample'        : switch_with_sample,
            'stop_flow1'                : stop_flow1,
            'stop_flow2'                : stop_flow2,
            'purge_active'              : purge_active,
            }

        return settings

class SampleDialog(wx.Dialog):
    """
    Allows addition/editing of the buffer info in the buffer list
    """
    def __init__(self, parent, settings, *args, **kwargs):
        wx.Dialog.__init__(self, parent, *args,
            style=wx.RESIZE_BORDER|wx.CAPTION|wx.CLOSE_BOX, **kwargs)

        self.SetSize(self._FromDIP((300, 275)))

        self._create_layout(settings)

        self.Fit()
        self.CenterOnParent()

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _create_layout(self, settings):
        parent = self

        self._sample_name_ctrl = wx.TextCtrl(parent, size=self._FromDIP((120, -1)))
        self._method_ctrl = wx.Choice(parent, choices=settings['all_acq_methods'])
        self._sample_loc_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)))
        self._inj_vol_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))
        self._elution_vol_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))
        self._rate_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))
        self._accel_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))
        self._pressure_lim_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))
        self._result_path_ctrl = wx.TextCtrl(parent, size=self._FromDIP((80, -1)))
        self._sample_method_ctrl = wx.Choice(parent,
            choices=['']+settings['all_sample_methods'])

        default_method = settings['acq_method']
        if default_method not in settings['all_acq_methods']:
            default_method = os.path.splitext(default_method)[0]+'.amx'

            if default_method not in settings['all_acq_methods']:
                default_method ='.\\{}'.format(default_method)

        if default_method in settings['all_acq_methods']:
            self._method_ctrl.SetStringSelection(default_method)
        else:
            self._method_ctrl.SetSelection(0)

        self._sample_loc_ctrl.SetValue(settings['sample_loc'])
        self._inj_vol_ctrl.SetValue(str(settings['inj_vol']))
        self._rate_ctrl.SetValue(str(settings['flow_rate']))
        self._elution_vol_ctrl.SetValue(str(settings['elution_vol']))
        self._accel_ctrl.SetValue(str(settings['flow_accel']))
        self._pressure_lim_ctrl.SetValue(str(settings['pressure_lim']))
        self._result_path_ctrl.SetValue(settings['result_path'])

        default_sp_method = settings['sp_method']
        if default_sp_method not in settings['all_sample_methods']:
            default_sp_method = os.path.splitext(default_sp_method)[0]+'.smx'

            if default_sp_method not in settings['all_sample_methods']:
                default_sp_method ='.\\{}'.format(default_sp_method)

        if default_sp_method in settings['all_sample_methods']:
            self._sample_method_ctrl.SetStringSelection(default_sp_method)
        else:
            self._sample_method_ctrl.SetSelection(0)

        equil_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        equil_sizer.Add(wx.StaticText(parent, label='Sample name:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer.Add(self._sample_name_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        equil_sizer.Add(wx.StaticText(parent, label='Method:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer.Add(self._method_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer.Add(wx.StaticText(parent, label='Sample location:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer.Add(self._sample_loc_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer.Add(wx.StaticText(parent, label='Injection volume (uL):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer.Add(self._inj_vol_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer.Add(wx.StaticText(parent, label='Elution volume (mL):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer.Add(self._elution_vol_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer.Add(wx.StaticText(parent, label='Elution rate (mL/min):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer.Add(self._rate_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer.Add(wx.StaticText(parent, label='Elution acceleration (mL/min^2):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer.Add(self._accel_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer.Add(wx.StaticText(parent, label='Pressure limit (bar):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer.Add(self._pressure_lim_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer.Add(wx.StaticText(parent, label='Result path:'),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        equil_sizer.Add(self._result_path_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer.Add(wx.StaticText(parent, label='Sample prep. method:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        equil_sizer.Add(self._sample_method_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)


        self._wait_for_flow_ramp_ctrl = wx.CheckBox(parent,
            label='Wait for flow to ramp before injection')
        self._settle_time_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))

        self._wait_for_flow_ramp_ctrl.SetValue(settings['wait_for_flow_ramp'])
        self._settle_time_ctrl.SetValue(str(settings['settle_time']))

        settle_sizer = wx.BoxSizer(wx.HORIZONTAL)
        settle_sizer.Add(wx.StaticText(parent, label='Settle time after ramp (s):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        settle_sizer.Add(self._settle_time_ctrl,
            flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL, border=self._FromDIP(5))

        button_sizer = self.CreateButtonSizer(wx.OK | wx.CANCEL)

        top_sizer=wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(equil_sizer, flag=wx.ALL, border=self._FromDIP(5))
        top_sizer.Add(self._wait_for_flow_ramp_ctrl,
            flag=wx.BOTTOM|wx.LEFT|wx.RIGHT, border=self._FromDIP(5))
        top_sizer.Add(settle_sizer, flag=wx.BOTTOM|wx.LEFT|wx.RIGHT,
            border=self._FromDIP(5))
        top_sizer.Add(button_sizer, flag=wx.ALL|wx.ALIGN_RIGHT,
            border=self._FromDIP(10))

        self.SetSizer(top_sizer)

    def get_settings(self):
        rate = self._rate_ctrl.GetValue()
        elution_vol = self._elution_vol_ctrl.GetValue()
        accel = self._accel_ctrl.GetValue()

        sample_name = self._sample_name_ctrl.GetValue()
        method = self._method_ctrl.GetStringSelection()
        sample_loc = self._sample_loc_ctrl.GetValue()
        inj_vol = self._inj_vol_ctrl.GetValue()
        pressure_lim = self._pressure_lim_ctrl.GetValue()
        result_path = self._result_path_ctrl.GetValue()
        sp_method = self._sample_method_ctrl.GetStringSelection()
        wait_for_ramp = self._wait_for_flow_ramp_ctrl.GetValue()
        settle_time = self._settle_time_ctrl.GetValue()

        if sp_method == '':
            sp_method = None

        settings = {
            'sample_name'           : sample_name,
            'acq_method'            : method,
            'sample_loc'            : sample_loc,
            'inj_vol'               : inj_vol,
            'flow_rate'             : rate,
            'elution_vol'           : elution_vol,
            'flow_accel'            : accel,
            'pressure_lim'          : pressure_lim,
            'result_path'           : result_path,
            'sp_method'             : sp_method,
            'wait_for_flow_ramp'    : wait_for_ramp,
            'settle_time'           : settle_time,
            }

        return settings

class SealWashDialog(wx.Dialog):
    """
    Allows addition/editing of the buffer info in the buffer list
    """
    def __init__(self, parent, settings, *args, **kwargs):
        wx.Dialog.__init__(self, parent, *args,
            style=wx.RESIZE_BORDER|wx.CAPTION|wx.CLOSE_BOX, **kwargs)

        self.SetSize(self._FromDIP((300, 200)))

        self._create_layout(settings)

        self.CenterOnParent()

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _create_layout(self, settings):
        parent = self

        self._mode_ctrl = wx.Choice(parent, choices=['Off', 'Single', 'Periodic'])
        self._single_duration_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))
        self._period_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))
        self._period_duration_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))

        self._mode_ctrl.SetStringSelection(settings['mode'])
        if float(settings['single_duration']) != -1:
            self._single_duration_ctrl.SetValue(str(settings['single_duration']))
        if float(settings['period']) != -1:
            self._period_ctrl.SetValue(str(settings['period']))
        if float(settings['period_duration']) != -1:
            self._period_duration_ctrl.SetValue(str(settings['period_duration']))

        wash_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        wash_sizer.Add(wx.StaticText(parent, label='Mode:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        wash_sizer.Add(self._mode_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        wash_sizer.Add(wx.StaticText(parent, label='Single duration (min):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        wash_sizer.Add(self._single_duration_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        wash_sizer.Add(wx.StaticText(parent, label='Period (min):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        wash_sizer.Add(self._period_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        wash_sizer.Add(wx.StaticText(parent, label='Period duration (min):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        wash_sizer.Add(self._period_duration_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)

        button_sizer = self.CreateButtonSizer(wx.OK | wx.CANCEL)

        top_sizer=wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(wash_sizer, flag=wx.ALL, border=self._FromDIP(5))
        top_sizer.Add(button_sizer ,flag=wx.BOTTOM|wx.RIGHT|wx.LEFT|wx.ALIGN_RIGHT,
            border=self._FromDIP(10))

        self.SetSizer(top_sizer)

    def get_settings(self):
        mode = self._mode_ctrl.GetStringSelection()
        single_duration = self._single_duration_ctrl.GetValue()
        period = self._period_ctrl.GetValue()
        period_duration = self._period_duration_ctrl.GetValue()

        settings = {
            'mode'              : mode,
            'single_duration'   : single_duration,
            'period'            : period,
            'period_duration'   : period_duration,
            }

        return settings

class RunList(wx.ListCtrl, wx.lib.mixins.listctrl.ListCtrlAutoWidthMixin):

    def __init__(self, *args, **kwargs):
        wx.ListCtrl.__init__(self, *args, **kwargs)
        self.InsertColumn(0, 'Status')
        self.InsertColumn(1, 'Name')

        wx.lib.mixins.listctrl.ListCtrlAutoWidthMixin.__init__(self)


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


######################################################
# Default settings

# SEC-SAXS 2 pump
hplc_args = {
    'name'  : 'SEC-SAXS',
    'args'  : ['AgilentHPLC', 'net.pipe://localhost/Agilent/OpenLAB/'],
    'kwargs': {'instrument_name': 'SEC-SAXS', 'project_name': 'Demo',
                'get_inst_method_on_start': True, 'use_angi': False,}
    }

default_selector_valve_args = {
    'name'  : 'Selector',
    'args'  : ['Cheminert', 'COM5'],
    'kwargs': {'positions' : 2}
    }

default_outlet_valve_args = {
    'name'  : 'Outlet',
    'args'  : ['Cheminert', 'COM3'],
    'kwargs': {'positions' : 2}
    }

default_purge1_valve_args = {
    'name'  : 'Purge 1',
    'args'  : ['Cheminert', 'COM6'],
    'kwargs': {'positions' : 4}
    }

default_purge2_valve_args = {
    'name'  : 'Purge 2',
    'args'  : ['Cheminert', 'COM9'],
    'kwargs': {'positions' : 4}
    }

default_buffer1_valve_args = {
    'name'  : 'Buffer 1',
    'args'  : ['Cheminert', 'COM7'],
    'kwargs': {'positions' : 10}
    }

default_buffer2_valve_args = {
    'name'  : 'Buffer 2',
    'args'  : ['Cheminert', 'COM4'],
    'kwargs': {'positions' : 10}
    }

# 2 pump HPLC for SEC-SAXS
setup_devices = [
    {'name': 'SEC-SAXS', 'args': ['AgilentHPLC2Pumps', None],
        'kwargs': {'hplc_args' : hplc_args,
        'selector_valve_args' : default_selector_valve_args,
        'outlet_valve_args' : default_outlet_valve_args,
        'purge1_valve_args' : default_purge1_valve_args,
        'purge2_valve_args' : default_purge2_valve_args,
        'buffer1_valve_args' : default_buffer1_valve_args,
        'buffer2_valve_args' : default_buffer2_valve_args,
        'pump1_id' : 'quat. pump 1#1c#1',
        'pump2_id' : 'quat. pump 2#1c#2'},
        }
    ]

default_hplc_2pump_settings = {
    # Connection settings for hplc
    'remote'        : False,
    'remote_device' : 'hplc',
    'device_init'   : setup_devices,
    'remote_ip'     : '192.168.1.16',
    'remote_port'   : '5558',
    'com_thread'    : None,
    # Default settings for hplc
    'purge_volume'              : 20,
    'purge_rate'                : 5,
    'purge_accel'               : 10,
    'purge_max_pressure'        : 250,
    'restore_flow_after_purge'  : True,
    'purge_with_sample'         : False,
    'stop_before_purge'         : True,
    'stop_after_purge'          : True,
    'equil_volume'              : 48,
    'equil_rate'                : 0.6,
    'equil_accel'               : 0.1,
    'equil_purge'               : True,
    'equil_with_sample'         : False,
    'stop_after_equil'          : False,
    'switch_purge_active'       : True,
    'switch_purge_volume'       : 1,
    'switch_purge_rate'         : 1,
    'switch_purge_accel'        : 10,
    'switch_with_sample'        : False,
    'switch_stop_flow1'         : True,
    'switch_stop_flow2'         : True,
    'restore_flow_after_switch' : True,
    'acq_method'                : 'SECSAXS_test',
    # 'acq_method'                : 'SEC-MALS',
    'sample_loc'                : 'D2F-A1',
    'inj_vol'                   : 10.0,
    'flow_rate'                 : 0.6,
    'flow_accel'                : 0.1,
    'elution_vol'               : 30,
    'sample_pressure_lim'       : 60.0,
    'max_inj_vol'               : 400.0,
    'result_path'               : '',
    'sp_method'                 : '',
    'wait_for_flow_ramp'        : True,
    'settle_time'               : 0.0,
    }

if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.INFO)
    # h1.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)




    # # SEC-MALS HPLC-1
    # hplc_args = {
    #     'name'  : 'HPLC-1',
    #     'args'  : ['AgilentHPLC', 'net.pipe://localhost/Agilent/OpenLAB/'],
    #     'kwargs': {'instrument_name': 'HPLC-1', 'project_name': 'Demo',
    #                 'get_inst_method_on_start': True}
    #     }

    # purge1_valve_args = {
    #     'name'  : 'Purge 1',
    #     'args'  :['Rheodyne', 'COM5'],
    #     'kwargs': {'positions' : 6}
    #     }

    # buffer1_valve_args = {
    #     'name'  : 'Buffer 1',
    #     'args'  : ['Cheminert', 'COM3'],
    #     'kwargs': {'positions' : 10}
    #     }

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




    # # Standard stack for SEC-MALS
    # setup_devices = [
    #     {'name': 'HPLC-1', 'args': ['AgilentHPLCStandard', None],
    #         'kwargs': {'hplc_args' : hplc_args,
    #         'purge1_valve_args' : purge1_valve_args,
    #         'buffer1_valve_args' : buffer1_valve_args,
    #         'pump1_id' : 'quat. pump#1c#1',
    #         },
    #     }
    #     ]

    # Local
    com_thread = HPLCCommThread('HPLCComm')
    com_thread.start()
    default_hplc_2pump_settings['com_thread'] = com_thread

    # # Remote
    # com_thread = None
    # default_hplc_2pump_settings['com_thread'] = com_thread
    # default_hplc_2pump_settings['remote'] = True
    # default_hplc_2pump_settings['remote_device'] = 'hplc'
    # default_hplc_2pump_settings['remote_ip'] = '164.54.204.113'
    # default_hplc_2pump_settings['remote_port'] = '5556'


    app = wx.App()
    logger.debug('Setting up wx app')
    frame = HPLCFrame('HPLCFrame', default_hplc_2pump_settings, parent=None,
        title='HPLC Control')
    frame.Show()


    # hplc_cmd_q = deque()
    # hplc_return_q = deque()
    # hplc_status_q = deque()
    # com_thread.add_new_communication('hplc_test', hplc_cmd_q,
    #     hplc_return_q, hplc_status_q)
    # cmd = ['submit_sample', ['SEC-SAXS', 'test', 'SECSAXS_test', 'D2F-A1', 10.0,
    #     0.05, 0.1, 0.1, 60.0], {'result_path':'api_test'}]
    # cmd2 = ['submit_sample', ['SEC-SAXS', 'test2', 'SECSAXS_test', 'D2F-A1', 10.0,
    #     0.05, 0.1, 0.1, 60.0], {'result_path':'api_test'}]
    # hplc_cmd_q.append(cmd)
    # # time.sleep(1)
    # hplc_cmd_q.append(cmd2)

    app.MainLoop()

    if com_thread is not None:
        com_thread.stop()
        com_thread.join()
