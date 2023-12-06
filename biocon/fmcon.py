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
import ctypes
import copy
import traceback

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import wx
import serial.tools.list_ports as list_ports

# sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_03_00\\DLL64\\Elveflow64DLL') #add the path of the library here
# sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_03_00\\python_64')#add the path of the LoadElveflow.py
# sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_03_00\\DLL32\\Elveflow32DLL') #add the path of the library here
# sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_03_00\\python_32')#add the path of the LoadElveflow.py

sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_07_02\\DLL64\\DLL64') #add the path of the library here
sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_07_02\\Python_64')#add the path of the LoadElveflow.py
sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_07_02\\DLL32\\DLL32') #add the path of the library here
sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_07_02\\Python_32')#add the path of the LoadElveflow.py

try:
    import Elveflow64 as Elveflow
except Exception:
    traceback.print_exc()
    try:
        import Elveflow32 as Elveflow
    except Exception:
        pass

import utils

class FlowMeter(object):
    """
    This class contains the settings and communication for a generic flow meter.
    It is intended to be subclassed by other flow meter classes, which contain
    specific information for communicating with a given pump. A flow meter object
    can be wrapped in a thread for using a GUI, implimented in :py:class:`PumpCommThread`
    or it can be used directly from the command line. The :py:class:`M5Pump`
    documentation contains an example.
    """

    def __init__(self, name, device, base_units, comm_lock=None):
        """
        :param str device: The device comport

        :param str name: A unique identifier for the pump

        :param str base_unis: Units reported by the flow meter. Should be one
            of: nL/s, nL/min, uL/s, uL/min, mL/s, mL/min
        """


        self.device = device
        self.name = name
        self._base_units = base_units
        self._units = self._base_units
        self._flow_mult = 1.

        self.connected = False

        if comm_lock is None:
            self.comm_lock = threading.Lock()
        else:
            self.comm_lock = comm_lock


    def __repr__(self):
        return '{}({}, {})'.format(self.__class__.__name__, self.name, self.device)

    def __str__(self):
        return '{} {}, connected to {}'.format(self.__class__.__name__, self.name, self.device)

    def connect(self):
        # Overwrite
        if not self.connected:
            self.connected = True

    @property
    def flow_rate(self):
        """
        Gets flow rate in units specified by ``FlowMeter.units``.
        Can be set while the pump is moving, and it will update the flow rate
        appropriately.

        :type: float
        """
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
        old_units = copy.copy(self._units)
        self._units = units

        if units in ['nL/s', 'nL/min', 'uL/s', 'uL/min', 'mL/s', 'mL/min']:
            self._units = units
            base_vu, base_tu = self._base_units.split('/')
            new_vu, new_tu = self._units.split('/')
            if base_vu != new_vu:
                if (base_vu == 'nL' and new_vu == 'uL') or (base_vu == 'uL' and new_vu == 'mL'):
                    self._flow_mult = 1./1000.
                elif base_vu == 'nL' and new_vu == 'mL':
                    self._flow_mult = 1./1000000.
                elif (base_vu == 'mL' and new_vu == 'uL') or (base_vu == 'uL' and new_vu == 'nL'):
                    self._flow_mult = 1000.
                elif base_vu == 'mL' and new_vu == 'nL':
                    self._flow_mult = 1000000.
            else:
                self._flow_mult = 1.

            if base_tu != new_tu:
                if base_tu == 'min':
                    self._flow_mult = self._flow_mult/60.
                else:
                    self._flow_mult = self._flow_mult*60.

            logger.info("Changed flow meter %s units from %s to %s", self.name, old_units, units)
        else:
            logger.warning("Failed to change flow meter %s units, units supplied were invalid: %s", self.name, units)

    def stop(self):
        pass

    def disconnect(self):
        pass

class BFS(FlowMeter):
    """
    This class contains information for initializing and communicating with
    a Elveflow Bronkhurst FLow Sensor (BFS), communicating via the Elveflow SDK.
    Below is an example that starts communication and prints the flow rate. ::

        >>> my_bfs = BFS("ASRL8::INSTR".encode('ascii'), 'BFS1')
        >>> print(my_bfs.flow_rate)
    """

    def __init__(self, name, device, comm_lock=None):
        """
        This makes the initial serial connection, and then sets the MForce
        controller parameters to the correct values.

        :param str device: The device comport as sent to pyserial

        :param str name: A unique identifier for the pump

        :param float bfs_filter: Smoothing factor for measurement. 1 = minimum
            filter, 0.00001 = maximum filter. Defaults to 0.5
        """

        FlowMeter.__init__(self, name, device, 'uL/min', comm_lock)

        logstr = ("Initializing flow meter {} on port {}".format(self.name,
            self.device))
        logger.info(logstr)

        self.connect()

        self.remote = False

        self.filter = 0.5

    def connect(self):
        if not self.connected:
            com = self.device.lstrip('COM')
            self.api_device = "ASRL{}::INSTR".format(com).encode('ascii')

            self.instr_ID = ctypes.c_int32()
            with self.comm_lock:
                error = Elveflow.BFS_Initialization(self.api_device,
                    ctypes.byref(self.instr_ID))

            self._check_error(error)

            self.connected = True

    @property
    def flow_rate(self):
        if not self.remote:
            self.density

            flow = ctypes.c_double(-1)
            with self.comm_lock:
                error = Elveflow.BFS_Get_Flow(self.instr_ID.value,
                    ctypes.byref(flow))

            self._check_error(error)

            flow = float(flow.value)

        else:
            # self._set_remote_params(True, True)
            flow, density, temp = self._read_remote()

        flow = flow*self._flow_mult

        logger.debug('Flow rate ({}): {}'.format(self.units, flow))

        return flow

    @property
    def density(self):
        if not self.remote:
            density = ctypes.c_double(-1)
            with self.comm_lock:
                error = Elveflow.BFS_Get_Density(self.instr_ID.value,
                    ctypes.byref(density))

            self._check_error(error)

            density = float(density.value)

        else:
            # self._set_remote_params(True, True)
            flow, density, temp = self._read_remote()

        logger.debug('Density: {}'.format(density))

        return density

    @property
    def temperature(self):
        if not self.remote:
            temperature = ctypes.c_double(-1)
            with self.comm_lock:
                error = Elveflow.BFS_Get_Temperature(self.instr_ID.value,
                    ctypes.byref(temperature))

            self._check_error(error)

            temperature = float(temperature.value)

        else:
            # self._set_remote_params(True, True)
            flow, density, temperature = self._read_remote()

        logger.debug('Temperature: {}'.format(temperature))

        return temperature

    @property
    def filter(self):
        return self._filter

    @filter.setter
    def filter(self, bfs_filter):
        self._filter = bfs_filter

        if not self.remote:
            cfilter = ctypes.c_double(self._filter) #convert to c_double
            with self.comm_lock:
                error = Elveflow.BFS_Set_Filter(self.instr_ID.value, cfilter)

            self._check_error(error)

        else:
            self._set_remote_params(True, True)

    def start_remote(self):
        with self.comm_lock:
            error = Elveflow.BFS_Start_Remote_Measurement(self.instr_ID.value)

        self._set_remote_params(True, True)

        self._check_error(error)

        self.remote = True

    def stop_remote(self):
        with self.comm_lock:
            error = Elveflow.BFS_Stop_Remote_Measurement(self.instr_ID.value)

        self._check_error(error)

        self.remote = False

    def _read_remote(self):
        data_sens=ctypes.c_double()
        data_dens=ctypes.c_double()
        data_temp=ctypes.c_double()

        with self.comm_lock:
            error = Elveflow.BFS_Get_Remote_Data(self.instr_ID.value,
                ctypes.byref(data_sens), ctypes.byref(data_dens),
                ctypes.byref(data_temp))

        self._check_error(error)

        flow = float(data_sens.value)
        density = float(data_dens.value)
        temp = float(data_temp.value)

        return flow, density, temp

    def _set_remote_params(self, read_density, read_temp):
        filt = ctypes.c_double(self.filter)

        if read_temp:
            m_temp = ctypes.c_int32(1)
        else:
            m_temp = ctypes.c_int32(0)

        if read_density:
            m_density = ctypes.c_int32(1)
        else:
            m_density = ctypes.c_int32(0)

        Elveflow.BFS_Set_Remote_Params(self.instr_ID.value, filt, m_temp,
            m_density)


    def _check_error(self, error):
        error = int(error)

        if error in utils.elveflow_errors:
            logger.error('%s Error: %s', self.name, utils.elveflow_errors[error])
        elif error != 0:
            logger.error('%s Error: LabView Error Code %s', self.name, error)

    def stop(self):
        with self.comm_lock:
            Elveflow.BFS_Destructor(self.instr_ID.value)


class SoftFlowMeter(FlowMeter):
    """
    This class contains the settings and communication for a generic flow meter.
    It is intended to be subclassed by other flow meter classes, which contain
    specific information for communicating with a given pump. A flow meter object
    can be wrapped in a thread for using a GUI, implimented in :py:class:`PumpCommThread`
    or it can be used directly from the command line. The :py:class:`M5Pump`
    documentation contains an example.
    """

    def __init__(self, name, device=None):
        """
        :param str device: The device comport

        :param str name: A unique identifier for the pump

        :param str base_unis: Units reported by the flow meter. Should be one
            of: nL/s, nL/min, uL/s, uL/min, mL/s, mL/min
        """
        FlowMeter.__init__(self, name, device, 'mL/min')

        self._flow_rate = 0

        self.density = 1
        self.temperature = 20

        self.connect()

    @property
    def flow_rate(self):
        """
        Gets flow rate in units specified by ``FlowMeter.units``.
        Can be set while the pump is moving, and it will update the flow rate
        appropriately.

        :type: float
        """
        return self._flow_rate

    @flow_rate.setter
    def flow_rate(self, rate):
        with self.comm_lock:
            self._flow_rate = rate

class FlowMeterCommThread(utils.CommManager):
    """
    This class creates a control thread for flow meters attached to the system.
    This thread is designed for using a GUI application. For command line
    use, most people will find working directly with a flow meter object much
    more transparent.
    """

    def __init__(self, name):
        """
        Initializes the custom thread.
        """
        utils.CommManager.__init__(self, name)

        logger.info("Starting flow meter control thread: %s", self.name)

        self._commands = {
            'connect'                       : self._connect_device,
            'get_flow_rate'                 : self._get_flow_rate,
            'get_units'                     : self._get_units,
            'set_units'                     : self._set_units,
            'get_density'                   : self._get_density,
            'get_temperature'               : self._get_temperature,
            'disconnect'                    : self._disconnect_device,
            'get_fr_multi'                  : self._get_flow_rate_multiple,
            'get_all_multi'                 : self._get_all_multiple,
            'set_flow_rate'                 : self._set_flow_rate, #Simulations only!
            'get_density_and_temperature'   : self._get_density_and_temperature,
            'get_filter'                    : self._get_filter,
            'set_filter'                    : self._set_filter,
            'get_settings'                  : self._get_settings,
            'get_bfs_instr_id'              : self._get_bfs_instr_id,
            'start_remote'                  : self._start_remote,
            }

        self._connected_devices = OrderedDict()
        self._connected_coms = OrderedDict()

        self.known_devices = {
            'BFS' : BFS,
            'Soft'  : SoftFlowMeter,
            }

    def _cleanup_devices(self):
        for device in self._connected_devices.values():
            device.stop()

    def _additional_new_comm(self, name):
        pass

    def _get_flow_rate(self, name, **kwargs):
        """
        This method gets the flow rate measured by a flow meter.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.
        """
        logger.debug("Getting flow meter %s flow rate", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.flow_rate

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Flow meter %s flow rate: %f", name, val)

    def _set_flow_rate(self, name, val, **kwargs):
        """
        This method sets the flow rate measured by a simulated flow meter.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.
        """
        logger.debug("Setting flow meter %s flow rate", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.flow_rate = val

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Flow meter %s flow rate set to: %f", name, val)

    def _get_density(self, name, **kwargs):
        """
        This method gets the flow rate measured by a flow meter.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.

        .. note:: Only the BFS flow meters can read density as well as flow rate.
        """
        logger.debug("Getting flow meter %s density", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.density

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Flow meter %s density: %f", name, val)

    def _get_temperature(self, name, **kwargs):
        """
        This method gets the flow rate measured by a flow meter.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.

        .. note:: Only the BFS flow meters can read temperature as well as flow rate.
        """
        logger.debug("Getting flow meter %s temperature", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.temperature

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Flow meter %s temperature: %f", name, val)

    def _get_units(self, name, **kwargs):
        """
        This method gets the flow rate measured by a flow meter.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.

        .. note:: Only the BFS flow meters can read units as well as flow rate.
        """
        logger.debug("Getting flow meter %s units", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.units

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Flow meter %s units: %s", name, val)

    def _set_units(self, name, val, **kwargs):
        """
        This method sets the units for the flow rate for a flow meter. This
        can be set to: nL/s, nL/min, uL/s, uL/min, mL/s, mL/min.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.

        :param str val: The units for the fm.
        """
        logger.info("Setting flow meter %s units", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.units = val

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Flow meter %s units set", name)

    def _get_flow_rate_multiple(self, names, **kwargs):
        """
        This method gets the flow rate measured by a flow meter.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.
        """
        logger.debug("Getting multiple flow rates")

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        flow_rates = []
        for name in names:
            device = self._connected_devices[name]
            val = device.flow_rate
            flow_rates.append(val)

        self._return_value((names, cmd, [names, flow_rates]),
            comm_name)

    def _get_all_multiple(self, names, **kwargs):
        """
        This method gets the flow rate measured by a flow meter.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.
        """
        logger.debug("Getting multiple readouts")

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        vals = []
        for name in names:
            fm = self._connected_devices[name]
            if isinstance(fm, BFS):
                density = fm.density
                temperature = fm.temperature
            else:
                density = None
                temperature = None
            flow_rate = fm.flow_rate
            vals.append((flow_rate, density, temperature))

        self._return_value((names, cmd, [names, vals]), comm_name)

    def _get_density_and_temperature(self, name, **kwargs):
        logger.debug('Getting density and temperature')

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val_d = device.density
        val_t = device.temperature

        self._return_value((name, 'get_density_and_temperature', [val_d, val_t]),
            comm_name)

    def _get_filter(self, name, **kwargs):
        """
        This method gets the filter setting for a flow meter.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.
        """
        logger.debug("Getting flow meter %s filter", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.filter

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Flow meter %s filter: %f", name, val)

    def _set_filter(self, name, val, **kwargs):
        """
        This method sets the filter setting for a flow meter.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.
        """
        logger.debug("Setting flow meter %s filter", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.filter = val

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Flow meter %s filter set to: %f", name, val)

    def _get_settings(self, name, **kwargs):
        """
        This method gets the filter setting for a flow meter.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.
        """
        logger.debug("Getting flow meter %s settings", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        units = device.units

        try:
            filt = device.filter
        except Exception:
            filt = None

        ret_vals = {
            'units'     : units,
            'filter'    : filt,
            }

        self._return_value((name, cmd, ret_vals), comm_name)

        logger.debug("Flow meter %s settings: %s", name, ret_vals)

    def _get_bfs_instr_id(self, name, **kwargs):
        """
        This method gets the filter setting for a flow meter.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.
        """
        logger.debug("Getting bfs instr_ID for %s", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.instr_ID

        self._return_value((name, cmd, val), comm_name)

    def _start_remote(self, name, **kwargs):
        """
        This method gets the filter setting for a flow meter.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.
        """
        logger.debug("Starting remote mode for %s", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.start_remote()

        self._return_value((name, cmd, True), comm_name)

class FlowMeterPanel(utils.DevicePanel):
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
    def __init__(self, parent, panel_id, settings, *args,
        **kwargs):
        """
        Initializes the custom thread. Important parameters here are the
        list of known commands ``_commands`` and known pumps ``known_fms``.

        :param wx.Window parent: Parent class for the panel.

        :param int panel_id: wx ID for the panel.

        """

        super(FlowMeterPanel, self).__init__(parent, panel_id, settings,
            *args, **kwargs)

    def _create_layout(self):
        """Creates the layout for the panel."""
        self.status = wx.StaticText(self, label='Not connected')

        status_grid = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(2),
            hgap=self._FromDIP(5))
        status_grid.AddGrowableCol(1)
        status_grid.Add(wx.StaticText(self, label='Flow meter name:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(wx.StaticText(self, label=self.name), 1,
            flag=wx.EXPAND|wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(wx.StaticText(self, label='Status: '))
        status_grid.Add(self.status, 1, flag=wx.EXPAND|wx.ALIGN_CENTER_HORIZONTAL)

        status_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Info'),
            wx.VERTICAL)
        status_sizer.Add(status_grid, 1, wx.EXPAND)

        self.vol_unit_ctrl = wx.Choice(self, choices=['nL', 'uL', 'mL'])
        self.vol_unit_ctrl.SetSelection(1)
        self.time_unit_ctrl = wx.Choice(self, choices=['s', 'min'])
        self.time_unit_ctrl.SetSelection(1)

        self.vol_unit_ctrl.Bind(wx.EVT_CHOICE, self._on_units)
        self.time_unit_ctrl.Bind(wx.EVT_CHOICE, self._on_units)

        gen_settings_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(2),
            hgap=self._FromDIP(5))
        gen_settings_sizer.AddGrowableCol(1)
        gen_settings_sizer.Add(wx.StaticText(self, label='Volume unit:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        gen_settings_sizer.Add(self.vol_unit_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        gen_settings_sizer.Add(wx.StaticText(self, label='Time unit:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        gen_settings_sizer.Add(self.time_unit_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)


        self.flow_rate = wx.StaticText(self, size=self._FromDIP((60, -1)),
            style=wx.ST_NO_AUTORESIZE)
        self.flow_units_lbl = wx.StaticText(self)

        self.gen_results_sizer = wx.FlexGridSizer(cols=3, vgap=self._FromDIP(2),
            hgap=self._FromDIP(5))
        self.gen_results_sizer.AddGrowableCol(1)
        self.gen_results_sizer.Add(wx.StaticText(self, label='Flow rate:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.gen_results_sizer.Add(self.flow_rate, 1,
            flag=wx.EXPAND|wx.ALIGN_CENTER_VERTICAL)
        self.gen_results_sizer.Add(self.flow_units_lbl, flag=wx.ALIGN_CENTER_VERTICAL)


        ###BFS specific stuff
        self.bfs_filter = utils.ValueEntry(self._on_filter, self,
            validator=utils.CharValidator('float_pos_te'))

        self.bfs_settings_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(2),
            hgap=self._FromDIP(5))
        self.bfs_settings_sizer.AddGrowableCol(1)
        self.bfs_settings_sizer.Add(wx.StaticText(self, label='Filter:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.bfs_settings_sizer.Add(self.bfs_filter,1,
            flag=wx.EXPAND|wx.ALIGN_CENTER_VERTICAL)

        self.bfs_density = wx.StaticText(self, size=self._FromDIP((60, -1)),
            style=wx.ST_NO_AUTORESIZE)
        self.bfs_temperature = wx.StaticText(self, size=self._FromDIP((60, -1)),
            style=wx.ST_NO_AUTORESIZE)

        self.density_label = wx.StaticText(self, label='Density:')
        self.density_units = wx.StaticText(self, label='g/L')
        self.temperature_label = wx.StaticText(self, label='Temperature:')
        self.temperature_units = wx.StaticText(self, label='°C')

        self.gen_results_sizer.Add(self.density_label, flag=wx.ALIGN_CENTER_VERTICAL)
        self.gen_results_sizer.Add(self.bfs_density, flag=wx.ALIGN_CENTER_VERTICAL)
        self.gen_results_sizer.Add(self.density_units, flag=wx.ALIGN_CENTER_VERTICAL)
        self.gen_results_sizer.Add(self.temperature_label, flag=wx.ALIGN_CENTER_VERTICAL)
        self.gen_results_sizer.Add(self.bfs_temperature, flag=wx.ALIGN_CENTER_VERTICAL)
        self.gen_results_sizer.Add(self.temperature_units, flag=wx.ALIGN_CENTER_VERTICAL)
        ###End BFS specific stuff


        self.settings_box_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Settings'),
            wx.VERTICAL)
        self.settings_box_sizer.Add(gen_settings_sizer, flag=wx.EXPAND)
        self.settings_box_sizer.Add(self.bfs_settings_sizer, flag=wx.EXPAND|wx.TOP,
            border=self._FromDIP(2))

        self.results_box_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Readings'),
            wx.VERTICAL)
        self.results_box_sizer.Add(self.gen_results_sizer, flag=wx.EXPAND)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(status_sizer, flag=wx.EXPAND)
        top_sizer.Add(self.results_box_sizer, flag=wx.EXPAND)
        top_sizer.Add(self.settings_box_sizer, flag=wx.EXPAND)

        vol_unit = self.vol_unit_ctrl.GetStringSelection()
        t_unit = self.time_unit_ctrl.GetStringSelection()
        self.flow_units_lbl.SetLabel('{}/{}'.format(vol_unit, t_unit))
        self.Refresh()

        self.SetSizer(top_sizer)

        return

    def _init_device(self, settings):
        """
        Initializes the flow meter.
        """
        device_data = settings['device_data']
        args = device_data['args']
        kwargs = device_data['kwargs']

        args.insert(0, self.name)

        self.fm_type = args[1]

        if self.fm_type != 'BFS':
            self.settings_box_sizer.Hide(self.bfs_settings_sizer, recursive=True)
            self.gen_results_sizer.Hide(self.density_label, recursive=True)
            self.gen_results_sizer.Hide(self.bfs_density, recursive=True)
            self.gen_results_sizer.Hide(self.density_units, recursive=True)
            self.gen_results_sizer.Hide(self.temperature_label, recursive=True)
            self.gen_results_sizer.Hide(self.bfs_temperature, recursive=True)
            self.gen_results_sizer.Hide(self.temperature_units, recursive=True)

        connect_cmd = ['connect', args, kwargs]

        self._send_cmd(connect_cmd, True)

        if self.fm_type == 'BFS':
            density_cmd = ['get_density', [self.name,], {}]
            ret = self._send_cmd(density_cmd, True)

            if ret is not None:
                self.bfs_density.SetLabel(str(ret))

            temperature_cmd = ['get_temperature', [self.name,], {}]
            ret = self._send_cmd(temperature_cmd, True)

            if ret is not None:
                self.bfs_temperature.SetLabel(str(ret))

            filter_cmd = ['get_filter', [self.name,], {}]
            ret = self._send_cmd(filter_cmd, True)

            if ret is not None:
                self.bfs_filter.SafeChangeValue(str(ret))

            d_and_t_cmd = ['get_density_and_temperature', [self.name,], {}]
            self._update_status_cmd(d_and_t_cmd, 1)

        units_cmd = ['get_units', [self.name,], {}]
        ret = self._send_cmd(units_cmd, True)

        if ret is not None:
            self._set_gui_units(ret)

        settings_cmd = ['get_settings', [self.name,], {}]
        self._update_status_cmd(settings_cmd, 5)

        flow_cmd = ['get_flow_rate', [self.name,], {}]
        self._update_status_cmd(flow_cmd, 1)

        self._set_status_label('Connected')

        logger.info('Initialized flow meter %s on startup', self.name)

    def _on_units(self, evt):
        """Called when the units are changed in the GUI."""
        self._set_units()

    def _set_units(self):
        vol_unit = self.vol_unit_ctrl.GetStringSelection()
        t_unit = self.time_unit_ctrl.GetStringSelection()

        units = '{}/{}'.format(vol_unit, t_unit)

        self.flow_units_lbl.SetLabel(units)

        units_cmd = ['set_units', [self.name, units], {}]
        self._send_cmd(units_cmd)

        logger.debug('Changed the flow meter units to %s and %s for flow meter %s', vol_unit, t_unit, self.name)

    def _set_gui_units(self, units):
        if units != self.flow_units_lbl.GetLabel():
            vol_u, t_u = units.split('/')

            self.vol_unit_ctrl.SetStringSelection(vol_u)
            self.time_unit_ctrl.SetStringSelection(t_u)
            self.flow_units_lbl.SetLabel(units)

    def _on_filter(self, obj, val):
        try:
            val = float(val)
        except Exception:
            return

        filter_cmd = ['set_filter', [self.name, val], {}]
        self._send_cmd(filter_cmd, True)

    def _set_status_label(self, status):
        """
        Changes the status in the GUI.

        :param str status: The status to display.
        """
        logger.debug('Setting flow meter %s status to %s', self.name, status)
        self.status.SetLabel(status)

    def _set_status(self, cmd, val):
        if cmd == 'get_flow_rate':
            if val is not None:
                val = str(round(val,3))

                if val != self.flow_rate.GetLabel():
                    self.flow_rate.SetLabel(val)

        elif cmd == 'get_density_and_temperature':
            if val is not None:
                val_d, val_t = val
                val_d = str(round(val_d,3))
                val_t = str(round(val_t,3))

                if val_d != self.bfs_density.GetLabel():
                    self.bfs_density.SetLabel(val_d)

                if val_t != self.bfs_temperature.GetLabel():
                    self.bfs_temperature.SetLabel(val_t)

        elif cmd == 'get_density':
            if val is not None:
                val = str(round(val,3))

                if val != self.bfs_density.GetLabel():
                    self.bfs_density.SetLabel(val)

        elif cmd == 'get_temperature':
            if val is not None:
                val = str(round(val,3))

                if val != self.bfs_temperature.GetLabel():
                    self.bfs_temperature.SetLabel(val)

        elif cmd == 'get_filter':
            if str(val) != self.bfs_filter.GetValue():
                self.bfs_filter.SafeChangeValue(str(val))

        elif cmd == 'get_units':
            self._set_gui_units(val)

        elif cmd == 'get_settings':
            units = val['units']
            filt = val['filter']

            self._set_gui_units(units)

            if filt is not None:
                if str(filt) != self.bfs_filter.GetValue():
                    self.bfs_filter.SafeChangeValue(str(filt))


class FlowMeterFrame(utils.DeviceFrame):

    def __init__(self, name, settings, *args, **kwargs):
        """
        Initializes the device frame. Takes frame name, utils.CommManager thread
        (or subclass), the device_panel class, and args and kwargs for the wx.Frame class.
        """
        super(FlowMeterFrame, self).__init__(name, settings, FlowMeterPanel,
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

    com_thread = FlowMeterCommThread('FlowComm')
    com_thread.start()

    # Remote
    # com_thread = None

    # my_bfs = BFS('COM8', 'BFS1')
    # my_bfs.filter = 1
    # my_bfs.flow_rate

    # fm_cmd_q = deque()
    # fm_return_q = deque()
    # abort_event = threading.Event()
    # my_fmcon = FlowMeterCommThread(fm_cmd_q, fm_return_q, abort_event, 'FMCon')
    # my_fmcon.start()
    #'ASRL8::INSTR'.encode('ascii')
    # init_cmd = ('connect', ('COM8', 'bfs1', 'BFS'), {})
    # fr_cmd = ('get_flow_rate', ('bfs1',), {})
    # d_cmd = ('get_density', ('bfs1',), {})
    # t_cmd = ('get_temperature', ('bfs1',), {})
    # units_cmd = ('set_units', ('bfs1', 'mL/min'), {})

    # fm_cmd_q.append(init_cmd)
    # fm_cmd_q.append(fr_cmd)
    # fm_cmd_q.append(d_cmd)
    # fm_cmd_q.append(t_cmd)
    # fm_cmd_q.append(units_cmd)
    # fm_cmd_q.append(fr_cmd)
    # time.sleep(5)
    # my_fmcon.stop()

    # Coflow flow meters
    setup_devices = [
        # {'name': 'sheath', 'args' : ['BFS', 'COM6'], 'kwargs': {}},
        {'name': 'outlet', 'args' : ['BFS', 'COM7'], 'kwargs': {}},
        ]

    # # Simulated flow meter
    # setup_devices = [
    #     {'name': 'sheath', 'args': ['Soft', None], 'kwargs': {}},
    #     ]

    settings = {
        'remote'        : False,
        'remote_device' : 'fm',
        'device_init'   : setup_devices,
        'remote_ip'     : '192.168.1.16',
        'remote_port'   : '5557',
        'com_thread'    : com_thread,
        }

    app = wx.App()
    logger.debug('Setting up wx app')
    frame = FlowMeterFrame('FMFrame', settings, parent=None,
        title='FM Control')
    frame.Show()
    app.MainLoop()

    if com_thread is not None:
        com_thread.stop()
        com_thread.join()
