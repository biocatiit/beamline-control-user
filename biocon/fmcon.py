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

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import wx
import serial.tools.list_ports as list_ports

#NOTE: RIGHT NOW, ONLY WORKS WITH 32bit elveflow stuff. The 64bit stuff seems to be broken.
sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_03_00\\DLL64\\Elveflow64DLL') #add the path of the library here
sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_03_00\\python_64')#add the path of the LoadElveflow.py
sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_03_00\\DLL32\\Elveflow32DLL') #add the path of the library here
sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_03_00\\python_32')#add the path of the LoadElveflow.py

try:
    import Elveflow64 as Elveflow
except Exception:
    try:
        import Elveflow32 as Elveflow
    except Exception:
        pass

print_lock = threading.RLock()


class FlowMeter(object):
    """
    This class contains the settings and communication for a generic flow meter.
    It is intended to be subclassed by other flow meter classes, which contain
    specific information for communicating with a given pump. A flow meter object
    can be wrapped in a thread for using a GUI, implimented in :py:class:`PumpCommThread`
    or it can be used directly from the command line. The :py:class:`M5Pump`
    documentation contains an example.
    """

    def __init__(self, device, name, base_units):
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

        self.units = self._base_units


    def __repr__(self):
        return '{}({}, {})'.format(self.__class__.__name__, self.name, self.device)

    def __str__(self):
        return '{} {}, connected to {}'.format(self.__class__.__name__, self.name, self.device)

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

class BFS(FlowMeter):
    """
    This class contains information for initializing and communicating with
    a Elveflow Bronkhurst FLow Sensor (BFS), communicating via the Elveflow SDK.
    Below is an example that starts communication and prints the flow rate. ::

        >>> my_bfs = BFS("ASRL8::INSTR".encode('ascii'), 'BFS1')
        >>> print(my_bfs.flow_rate)
    """

    def __init__(self, device, name, bfs_filter=1):
        """
        This makes the initial serial connection, and then sets the MForce
        controller parameters to the correct values.

        :param str device: The device comport as sent to pyserial

        :param str name: A unique identifier for the pump

        :param float bfs_filter: Smoothing factor for measurement. 1 = minimum
            filter, 0.00001 = maximum filter. Defaults to 1
        """
        com = device.lstrip('COM')
        device = "ASRL{}::INSTR".format(com).encode('ascii')
        FlowMeter.__init__(self, device, name, 'uL/min')
        logstr = ("Initializing flow meter {} on port {}".format(self.name,
            self.device))
        logger.info(logstr)

        self.instr_ID = ctypes.c_int32()
        error = Elveflow.BFS_Initialization(device,
            ctypes.byref(self.instr_ID))
        logger.exception('Initialization error: {}'.format(error))

        self._filter = bfs_filter


    @property
    def flow_rate(self):
        self.density

        flow = ctypes.c_double(-1)
        error = Elveflow.BFS_Get_Flow(self.instr_ID.value, ctypes.byref(flow))
        flow = float(flow.value)*self._flow_mult
        logger.debug('Flow rate ({}): {}'.format(self.units, flow))

        return flow

    @property
    def density(self):
        density = ctypes.c_double(-1)
        error = Elveflow.BFS_Get_Density(self.instr_ID.value, ctypes.byref(density))
        density = float(density.value)
        logger.debug('Density: {}'.format(density))

        return density

    @property
    def temperature(self):
        temperature = ctypes.c_double(-1)
        error = Elveflow.BFS_Get_Temperature(self.instr_ID.value, ctypes.byref(temperature))
        temperature = float(temperature.value)
        logger.debug('Temperature: {}'.format(temperature))

        return temperature

    @property
    def filter(self):
        return self._filter

    @filter.setter
    def filter(self, bfs_filter):
        self._filter = bfs_filter

        cfilter = ctypes.c_double(self._filter) #convert to c_double
        error = Elveflow.BFS_Set_Filter(self.instr_ID.value, cfilter)

    def stop(self):
        Elveflow.BFS_Destructor(self.instr_ID.value)

class FlowMeterCommThread(threading.Thread):
    """
    This class creates a control thread for flow meters attached to the system.
    This thread is designed for using a GUI application. For command line
    use, most people will find working directly with a flow meter object much
    more transparent. Below you'll find an example that initializes a
    :py:class:`BFS` and measures the flow. ::

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

    def __init__(self, command_queue, return_queue, abort_event, name=None):
        """
        Initializes the custom thread. Important parameters here are the
        list of known commands ``_commands`` and known pumps ``known_fms``.

        :param collections.deque command_queue: The queue used to pass commands
            to the thread.

        :param collections.deque return_queue: The queue used to return data
            from the thread.

        :param threading.Event abort_event: An event that is set when the thread
            needs to abort, and otherwise is not set.
        """
        threading.Thread.__init__(self, name=name)
        self.daemon = True

        logger.info("Starting flow meter control thread: %s", self.name)

        self.command_queue = command_queue
        self.return_queue = return_queue
        self._abort_event = abort_event
        self._stop_event = threading.Event()

        self._commands = {'connect'         : self._connect_fm,
                        'get_flow_rate'     : self._get_flow_rate,
                        'set_units'         : self._set_units,
                        'get_density'       : self._get_density,
                        'get_temperature'   : self._get_temperature,
                        'disconnect'        : self._disconnect,
                        'get_fr_multi'      : self._get_flow_rate_multiple,
                        'get_all_multi'     : self._get_all_multiple,
                        }

        self._connected_fms = OrderedDict()

        self.known_fms = {'BFS' : BFS,
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
                    msg = ("Flow meter control thread failed to run command '%s' "
                        "with args: %s and kwargs: %s " %(command,
                        ', '.join(['{}'.format(a) for a in args]),
                        ', '.join(['{}:{}'.format(kw, item) for kw, item in kwargs.items()])))
                    logger.exception(msg)

                    if command == 'connect' or command == 'disconnect':
                        self.return_queue.append((command, False))

            else:
                time.sleep(0.01)

        if self._stop_event.is_set():
            self._stop_event.clear()
        else:
            self._abort()

        for fm in self._connected_fms.values():
            fm.stop()

        logger.info("Quitting flow meter control thread: %s", self.name)

    def _connect_fm(self, device, name, fm_type, **kwargs):
        """
        This method connects to a flow meter by creating a new :py:class:`FlowMeter`
        subclass object (e.g. a new :py:class:`BFS` object). This pump is saved
        in the thread and can be called later to do stuff. All pumps must be
        connected before they can be used.

        :param str device: The device comport

        :param str name: A unique identifier for the pump

        :param str pump_type: A pump type in the ``known_fms`` dictionary.

        :param \*\*kwargs: This function accepts arbitrary keyword args that
            are passed directly to the :py:class:`FlowMeter` subclass that is
            called. For example, for a :py:class:`BFS` you could pass ``bfs_filter``.
        """
        logger.info("Connecting flow meter %s", name)
        new_fm = self.known_fms[fm_type](device, name, **kwargs)
        self._connected_fms[name] = new_fm
        logger.debug("Flow meter %s connected", name)

        self.return_queue.append(('connected', True))

    def _disconnect(self, name):
        logger.info("Disconnecting flow meter %s", name)
        fm = self._connected_fms[name]
        fm.stop()
        del self._connected_fms[name]
        logger.debug("Flow meter %s disconnected", name)

        self.return_queue.append(('disconnected', True))

    def _get_flow_rate(self, name):
        """
        This method gets the flow rate measured by a flow meter.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.
        """
        logger.debug("Getting flow meter %s flow rate", name)
        fm = self._connected_fms[name]
        flow_rate = fm.flow_rate
        logger.debug("Flow meter %s flow rate: %f", name, flow_rate)

        self.return_queue.append(('flow_rate', flow_rate))

    def _get_density(self, name):
        """
        This method gets the flow rate measured by a flow meter.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.

        .. note:: Only the BFS flow meters can read density as well as flow rate.
        """
        logger.debug("Getting flow meter %s density", name)
        fm = self._connected_fms[name]
        density = fm.density
        logger.debug("Flow meter %s density: %f", name, density)

        self.return_queue.append(('density', density))

    def _get_temperature(self, name):
        """
        This method gets the flow rate measured by a flow meter.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.

        .. note:: Only the BFS flow meters can read temperature as well as flow rate.
        """
        logger.debug("Getting flow meter %s temperature", name)
        fm = self._connected_fms[name]
        temperature = fm.temperature
        logger.debug("Flow meter %s temperature: %f", name, temperature)

        self.return_queue.append(('temperature', temperature))

    def _set_units(self, name, units):
        """
        This method sets the units for the flow rate for a flow meter. This
        can be set to: nL/s, nL/min, uL/s, uL/min, mL/s, mL/min.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.

        :param str units: The units for the fm.
        """
        logger.info("Setting flow meter %s units", name)
        fm = self._connected_fms[name]
        fm.units = units
        logger.debug("Flow meter %s units set", name)

    def _get_flow_rate_multiple(self, names):
        """
        This method gets the flow rate measured by a flow meter.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.
        """
        logger.debug("Getting multiple flow rates")
        flow_rates = []
        for name in names:
            fm = self._connected_fms[name]
            flow_rate = fm.flow_rate
            flow_rates.append(flow_rate)

        self.return_queue.append(('multi_flow', names, flow_rates))

    def _get_all_multiple(self, names):
        """
        This method gets the flow rate measured by a flow meter.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.
        """
        logger.debug("Getting multiple flow rates")
        vals = []
        for name in names:
            fm = self._connected_fms[name]
            density = fm.density
            temperature = fm.temperature
            flow_rate = fm.flow_rate
            vals.append((flow_rate, density, temperature))

        self.return_queue.append(('multi_flow', names, vals))

    def _abort(self):
        """
        Clears the ``command_queue`` and the ``return_queue``.
        """
        logger.info("Aborting flow meter control thread %s current and future commands", self.name)
        self.command_queue.clear()
        self.return_queue.clear()

        self._abort_event.clear()
        logger.debug("Flow meter control thread %s aborted", self.name)

    def stop(self):
        """Stops the thread cleanly."""
        logger.info("Starting to clean up and shut down flow meter control thread: %s", self.name)
        self._stop_event.set()

class FlowMeterPanel(wx.Panel):
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
    def __init__(self, parent, panel_id, panel_name, all_comports, fm_cmd_q,
        fm_return_q, known_fms, fm_name, fm_type=None, comport=None, fm_args=[],
        fm_kwargs={}):
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

        wx.Panel.__init__(self, parent, panel_id, name=panel_name)
        logger.debug('Initializing FlowMeterPanel for flow meter %s', fm_name)

        self.name = fm_name
        self.fm_cmd_q = fm_cmd_q
        self.all_comports = all_comports
        self.known_fms = known_fms
        self.answer_q = fm_return_q
        self.connected = False

        self.top_sizer = self._create_layout()

        self._flow_timer = wx.Timer(self)
        self._measurement_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_flow_timer, self._flow_timer)
        self.Bind(wx.EVT_TIMER, self._on_meas_timer, self._measurement_timer)

        self.SetSizer(self.top_sizer)

        self._initfm(fm_type, comport, fm_args, fm_kwargs)


    def _create_layout(self):
        """Creates the layout for the panel."""
        self.status = wx.StaticText(self, label='Not connected')

        status_grid = wx.FlexGridSizer(rows=2, cols=2, vgap=2, hgap=2)
        status_grid.AddGrowableCol(1)
        status_grid.Add(wx.StaticText(self, label='Flow meter name:'))
        status_grid.Add(wx.StaticText(self, label=self.name), 1, wx.EXPAND)
        status_grid.Add(wx.StaticText(self, label='Status: '))
        status_grid.Add(self.status, 1, wx.EXPAND)

        status_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Info'),
            wx.VERTICAL)
        status_sizer.Add(status_grid, 1, wx.EXPAND)

        self.type_ctrl = wx.Choice(self,
            choices=[item.replace('_', ' ') for item in self.known_fms.keys()],
            style=wx.CB_SORT)
        self.type_ctrl.SetSelection(0)
        self.com_ctrl = wx.Choice(self, choices=self.all_comports, style=wx.CB_SORT)
        self.vol_unit_ctrl = wx.Choice(self, choices=['nL', 'uL', 'mL'])
        self.vol_unit_ctrl.SetSelection(1)
        self.time_unit_ctrl = wx.Choice(self, choices=['s', 'min'])
        self.time_unit_ctrl.SetSelection(1)

        self.type_ctrl.Bind(wx.EVT_CHOICE, self._on_type)
        self.vol_unit_ctrl.Bind(wx.EVT_CHOICE, self._on_units)
        self.time_unit_ctrl.Bind(wx.EVT_CHOICE, self._on_units)

        gen_settings_sizer = wx.FlexGridSizer(rows=4, cols=2, vgap=2, hgap=2)
        gen_settings_sizer.AddGrowableCol(1)
        gen_settings_sizer.Add(wx.StaticText(self, label='Flow meter type:'))
        gen_settings_sizer.Add(self.type_ctrl, 1, wx.EXPAND)
        gen_settings_sizer.Add(wx.StaticText(self, label='COM port:'))
        gen_settings_sizer.Add(self.com_ctrl, 1, wx.EXPAND)
        gen_settings_sizer.Add(wx.StaticText(self, label='Volume unit:'))
        gen_settings_sizer.Add(self.vol_unit_ctrl)
        gen_settings_sizer.Add(wx.StaticText(self, label='Time unit:'))
        gen_settings_sizer.Add(self.time_unit_ctrl)


        self.flow_rate = wx.TextCtrl(self)
        self.flow_units_lbl = wx.StaticText(self)

        gen_results_sizer = wx.FlexGridSizer(rows=1, cols=3, vgap=2, hgap=2)
        gen_results_sizer.AddGrowableCol(1)
        gen_results_sizer.Add(wx.StaticText(self, label='Flow rate:'))
        gen_results_sizer.Add(self.flow_rate, 1, wx.EXPAND)
        gen_results_sizer.Add(self.flow_units_lbl)


        ###BFS specific stuff
        self.bfs_filter = wx.TextCtrl(self, value='0.001', style=wx.TE_PROCESS_ENTER)
        self.bfs_filter.Bind(wx.EVT_TEXT_ENTER, self._on_filter)

        self.bfs_settings_sizer = wx.FlexGridSizer(rows=1, cols=2, vgap=2, hgap=2)
        self.bfs_settings_sizer.AddGrowableCol(1)
        self.bfs_settings_sizer.Add(wx.StaticText(self, label='Filter:'))
        self.bfs_settings_sizer.Add(self.bfs_filter,1, wx.EXPAND)

        self.bfs_density = wx.TextCtrl(self)
        self.bfs_temperature = wx.TextCtrl(self)

        self.bfs_results_sizer = wx.FlexGridSizer(rows=2, cols=3, vgap=2, hgap=2)
        self.bfs_results_sizer.Add(wx.StaticText(self, label='Density:'))
        self.bfs_results_sizer.Add(self.bfs_density)
        self.bfs_results_sizer.Add(wx.StaticText(self, label='g/L'))
        self.bfs_results_sizer.Add(wx.StaticText(self, label='Temperature'))
        self.bfs_results_sizer.Add(self.bfs_temperature)
        self.bfs_results_sizer.Add(wx.StaticText(self, label='°C'))
        ###End BFS specific stuff

        self.connect_button = wx.Button(self, label='Connect')
        self.connect_button.Bind(wx.EVT_BUTTON, self._on_connect)

        self.settings_box_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Settings'),
            wx.VERTICAL)
        self.settings_box_sizer.Add(gen_settings_sizer, flag=wx.EXPAND)
        self.settings_box_sizer.Add(self.bfs_settings_sizer, flag=wx.EXPAND|wx.TOP, border=2)
        self.settings_box_sizer.Add(self.connect_button, flag=wx.ALIGN_CENTER_HORIZONTAL|wx.TOP, border=2)

        self.results_box_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Readings'),
            wx.VERTICAL)
        self.results_box_sizer.Add(gen_results_sizer, flag=wx.EXPAND)
        self.results_box_sizer.Add(self.bfs_results_sizer, flag=wx.EXPAND|wx.TOP, border=2)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(status_sizer, flag=wx.EXPAND)
        top_sizer.Add(self.settings_box_sizer, flag=wx.EXPAND)
        top_sizer.Add(self.results_box_sizer, flag=wx.EXPAND)

        if self.type_ctrl.GetStringSelection() != 'BFS':
            self.settings_box_sizer.Hide(self.bfs_settings_sizer, recursive=True)
            self.results_box_sizer.Hide(self.bfs_results_sizer, recursive=True)

        vol_unit = self.vol_unit_ctrl.GetStringSelection()
        t_unit = self.time_unit_ctrl.GetStringSelection()
        self.flow_units_lbl.SetLabel('{}/{}'.format(vol_unit, t_unit))
        self.Refresh()

        return top_sizer

    def _initfm(self, fm_type, comport, fm_args, fm_kwargs):
        """
        Initializes the flow meter parameters if any were provided. If enough are
        provided the flow meter is automatically connected.

        :param str fm_type: The flow meter type, corresponding to a ``known_fm``.

        :param str comport: The comport the flow meter is attached to.

        :param list fm_args: The flow meter positional initialization values.
            Appropriate values depend on the flow meter.

        :param dict fm_kwargs: The flow meter key word arguments. Appropriate
            values depend on the flow meter.
        """
        my_fms = [item.replace('_', ' ') for item in self.known_fms.keys()]
        if fm_type in my_fms:
            self.type_ctrl.SetStringSelection(fm_type)

        if comport in self.all_comports:
            self.com_ctrl.SetStringSelection(comport)

        if fm_type == 'BFS':
            if 'bfs_filter' in fm_kwargs.keys():
                self.bfs_filter.ChangeValue(fm_kwargs['bfs_filter'])

            if len(fm_args) >= 1:
                self.bfs_filter.ChangeValue(fm_args[0])

        if fm_type in my_fms and comport in self.all_comports:
            logger.info('Initialized flow meter %s on startup', self.name)
            self._connect()

    def _on_type(self, evt):
        """Called when the flow meter type is changed in the GUI."""
        fm = self.type_ctrl.GetStringSelection()
        logger.info('Changed the flow meter type to %s for flow meter %s', fm, self.name)

        if fm == 'BFS':
            self.settings_box_sizer.Show(self.bfs_settings_sizer, recursive=True)
            self.results_box_sizer.Show(self.bfs_results_sizer, recursive=True)

    def _on_units(self, evt):
        """Called when the units are changed in the GUI."""
        vol_unit = self.vol_unit_ctrl.GetStringSelection()
        t_unit = self.time_unit_ctrl.GetStringSelection()

        self.flow_units_lbl.SetLabel('{}/{}'.format(vol_unit, t_unit))

        self._send_cmd('set_units')

        logger.debug('Changed the flow meter units to %s and %s for flow meter %s', vol_unit, t_unit, self.name)

    def _on_filter(self, evt):
        pass #Needs to be done!

    def _on_connect(self, evt):
        """Called when a flow meter is connected in the GUI."""
        self._connect()

    def _connect(self):
        """Initializes the flow meter in the FlowMeterCommThread"""
        fm = self.type_ctrl.GetStringSelection().replace(' ', '_')

        if fm == 'BFS':
            try:
                float(self.bfs_filter.GetValue())
            except Exception:
                msg = "Calibration values must be numbers."
                wx.MessageBox(msg, "Error setting calibration values")
                logger.debug('Failed to connect to flow meter %s because the BFS calibration values were bad', self.name)
                return

        logger.info('Connected to flow meter %s', self.name)
        self.connected = True
        self.connect_button.SetLabel('Reconnect')
        self._send_cmd('connect')
        self._send_cmd('get_density')
        self._send_cmd('get_temperature')
        self._send_cmd('get_flow_rate')
        self._set_status('Connected')

        self._flow_timer.Start(200)
        answer_thread = threading.Thread(target=self._wait_for_answer)
        answer_thread.daemon = True
        answer_thread.start()
        self._measurement_timer.Start(5000)

        return

    def _set_status(self, status):
        """
        Changes the status in the GUI.

        :param str status: The status to display.
        """
        logger.debug('Setting flow meter %s status to %s', self.name, status)
        self.status.SetLabel(status)

    def _on_flow_timer(self, evt):
        """
        Called every 100 ms when the flow meter is connected. It gets the
        flow rate.
        """
        self._send_cmd('get_flow_rate')

    def _on_meas_timer(self, evt):
        if self.type_ctrl.GetStringSelection().replace(' ', '_') == 'BFS':
            self._send_cmd('get_density')
            self._send_cmd('get_temperature')

    def _send_cmd(self, cmd):
        """
        Sends commands to the pump using the ``fm_cmd_q`` that was given
        to :py:class:`FlowMeterCommThread`.

        :param str cmd: The command to send, matching the command in the
            :py:class:`FlowMeterCommThread` ``_commands`` dictionary.
        """
        logger.debug('Sending flow meter %s command %s', self.name, cmd)
        if cmd == 'get_flow_rate':
            self.fm_cmd_q.append(('get_flow_rate', (self.name,), {}))
        elif cmd == 'get_density':
            self.fm_cmd_q.append(('get_density', (self.name,), {}))
        elif cmd == 'get_temperature':
            self.fm_cmd_q.append(('get_temperature', (self.name,), {}))
        elif cmd == 'set_units':
            units = self.flow_units_lbl.GetLabel()
            self.fm_cmd_q.append(('set_units', (self.name, units), {}))
        elif cmd == 'connect':
            com = self.com_ctrl.GetStringSelection()
            fm = self.type_ctrl.GetStringSelection().replace(' ', '_')

            args = (com, self.name, fm)

            if fm == 'BFS':
                bfs_filter = float(self.bfs_filter.GetValue())
                kwargs = {'bfs_filter': bfs_filter}
            else:
                kwargs = {}

            self.fm_cmd_q.append(('connect', args, kwargs))

    def _wait_for_answer(self):
        while True:
            if len(self.answer_q) > 0:
                answer = self.answer_q.popleft()
                if answer[0] == 'flow_rate':
                    wx.CallAfter(self.flow_rate.ChangeValue, str(round(answer[1],3)))
                elif answer[0] == 'density':
                    wx.CallAfter(self.bfs_density.ChangeValue, str(round(answer[1],3)))
                elif answer[0] == 'temperature':
                    wx.CallAfter(self.bfs_temperature.ChangeValue, str(round(answer[1],3)))
            else:
                time.sleep(0.05)


class FlowMeterFrame(wx.Frame):
    """
    A lightweight frame allowing one to work with arbitrary number of flow meters.
    Only meant to be used when the fscon module is run directly,
    rather than when it is imported into another program.
    """
    def __init__(self, *args, **kwargs):
        """
        Initializes the flow meter frame. Takes args and kwargs for the wx.Frame class.
        """
        super(FlowMeterFrame, self).__init__(*args, **kwargs)
        logger.debug('Setting up the FlowMeterFrame')
        self.fm_cmd_q = deque()
        self.fm_return_q = deque()
        self.abort_event = threading.Event()
        self.fm_con = FlowMeterCommThread(self.fm_cmd_q, self.fm_return_q,
            self.abort_event, 'FMCon')
        self.fm_con.start()

        self.Bind(wx.EVT_CLOSE, self._on_exit)

        self._get_ports()

        self.fms =[]

        top_sizer = self._create_layout()

        self.SetSizer(top_sizer)

        self.Fit()
        self.Raise()

        self._initfms()

    def _create_layout(self):
        """Creates the layout"""
        fm_panel = FlowMeterPanel(self, wx.ID_ANY, 'stand_in', self.ports,
            self.fm_cmd_q, self.fm_return_q, self.fm_con.known_fms, 'stand_in')

        self.fm_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.fm_sizer.Add(fm_panel, flag=wx.RESERVE_SPACE_EVEN_IF_HIDDEN)

        self.fm_sizer.Hide(fm_panel, recursive=True)

        button_panel = wx.Panel(self)

        add_fm = wx.Button(button_panel, label='Add flow meter')
        add_fm.Bind(wx.EVT_BUTTON, self._on_addfm)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(add_fm)

        button_panel_sizer = wx.BoxSizer(wx.VERTICAL)
        button_panel_sizer.Add(wx.StaticLine(button_panel), flag=wx.EXPAND|wx.TOP|wx.BOTTOM, border=2)
        button_panel_sizer.Add(button_sizer, flag=wx.ALIGN_RIGHT|wx.BOTTOM|wx.RIGHT, border=2)

        button_panel.SetSizer(button_panel_sizer)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(self.fm_sizer, flag=wx.EXPAND)
        top_sizer.Add(button_panel, flag=wx.EXPAND)

        return top_sizer

    def _initfms(self):
        """
        This is a convenience function for initalizing flow meters on startup, if you
        already know what flow meters you want to add. You can comment it out in
        the ``__init__`` if you want to not load any flow meters on startup.

        If you want to add flow meters here, add them to the ``setup_fmss`` list.
        Each entry should be an iterable with the following parameters: name,
        flow meter type, comport, arg list, and kwarg dict in that order. How the
        arg list and kwarg dict are handled are defined in the
        :py:func:`FlowMeterPanel._initfm` function, and depends on the flow meter type.
        """
        if not self.fms:
            self.fm_sizer.Remove(0)

        setup_fms = [('3', 'BFS', 'COM5', [], {}),
            ('4', 'BFS', 'COM6', [], {}),
                    ]

        logger.info('Initializing %s flow meters on startup', str(len(setup_fms)))

        for fm in setup_fms:
            new_fm = FlowMeterPanel(self, wx.ID_ANY, fm[0], self.ports, self.fm_cmd_q,
                self.fm_return_q, self.fm_con.known_fms, fm[0], fm[1], fm[2], fm[3], fm[4])

            self.fm_sizer.Add(new_fm)
            self.fms.append(new_fm)

        self.Layout()
        self.Fit()

    def _on_addfm(self, evt):
        """
        Called when the Add Flow Meter button is used. Adds a new flow meter
        to the control panel.

        .. note:: FLow meter names must be distinct.
        """
        if not self.fms:
            self.fm_sizer.Remove(0)

        dlg = wx.TextEntryDialog(self, "Enter flow meter name:", "Create new flow meter")
        if dlg.ShowModal() == wx.ID_OK:
            name = dlg.GetValue()
            for fm in self.fms:
                if name == fm.name:
                    msg = "Flow meter names must be distinct. Please choose a different name."
                    wx.MessageBox(msg, "Failed to add flow meter")
                    logger.debug('Attempted to add a flow meter with the same name (%s) as another pump.', name)
                    return

            new_fm = FlowMeterPanel(self, wx.ID_ANY, name, self.ports, self.fm_cmd_q,
                self.fm_return_q, self.fm_con.known_fms, name)
            logger.info('Added new flow meter %s to the flow meter control panel.', name)
            self.fm_sizer.Add(new_fm)
            self.fms.append(new_fm)

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

        logger.debug('Found the following comports for the FlowMeterFrame: %s', ' '.join(self.ports))

    def _on_exit(self, evt):
        """Stops all current pump motions and then closes the frame."""
        logger.debug('Closing the FLowMeter')
        self.fm_con.stop()
        while self.fm_con.is_alive():
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

    app = wx.App()
    logger.debug('Setting up wx app')
    frame = FlowMeterFrame(None, title='FM Control')
    frame.Show()
    app.MainLoop()


