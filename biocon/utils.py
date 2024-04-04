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

import logging
import string
import os
import sys
import six
from six.moves import StringIO as bytesio
import platform
import threading
from collections import deque, OrderedDict
import time
import copy

logger = logging.getLogger(__name__)

import wx
from wx.lib.wordwrap import wordwrap
from wx.lib.stattext import GenStaticText as StaticText
import wx.lib.mixins.listctrl
from matplotlib.backends.backend_wxagg import NavigationToolbar2WxAgg
import numpy as np
import serial.tools.list_ports as list_ports

import client

class CharValidator(wx.Validator):
    ''' Validates data as it is entered into the text controls. '''

    def __init__(self, flag):
        wx.Validator.__init__(self)
        self.flag = flag
        self.Bind(wx.EVT_CHAR, self.OnChar)

        self.fname_chars = string.ascii_letters+string.digits+'_-'

        self.special_keys = [wx.WXK_BACK, wx.WXK_DELETE,
            wx.WXK_TAB, wx.WXK_NUMPAD_TAB, wx.WXK_NUMPAD_ENTER]

    def Clone(self):
        '''Required Validator method'''
        return CharValidator(self.flag)

    def Validate(self, win):
        return True

    def TransferToWindow(self):
        return True

    def TransferFromWindow(self):
        return True

    def OnChar(self, event):
        keycode = int(event.GetKeyCode())
        if keycode < 256 and keycode not in self.special_keys:
            #print keycode
            key = chr(keycode)
            #print key
            if self.flag == 'int' and key not in string.digits:
                return
            elif self.flag == 'int_te' and key not in string.digits+'\n\r':
                return
            elif self.flag == 'float' and key not in string.digits+'.':
                return
            elif self.flag == 'fname' and key not in self.fname_chars:
                return
            elif self.flag == 'float_te' and key not in string.digits+'-.\n\r':
                return
            elif self.flag == 'float_neg' and key not in string.digits+'.-':
                return
            elif self.flag == 'float_pos_te' and key not in string.digits+'.\n\r':
                return

        event.Skip()

def get_mxdir():
    """Gets the top level install directory for MX."""
    try:
        mxdir = os.environ["MXDIR"]
    except:
        mxdir = "/opt/mx"   # This is the default location.

    return mxdir

def get_mpdir():
    """Construct the name of the Mp modules directory."""
    mxdir = get_mxdir()

    mp_modules_dir = os.path.join(mxdir, "lib", "mp")
    mp_modules_dir = os.path.normpath(mp_modules_dir)

    return mp_modules_dir

def set_mppath():
    """Puts the mp directory in the system path, if it isn't already."""
    path = os.environ['PATH']

    mp_dir = get_mpdir()
    mx_dir = get_mxdir()

    if mp_dir not in path:
        os.environ["PATH"] = mp_dir+os.pathsep+os.environ["PATH"]
        sys.path.append(mp_dir)

    if mx_dir not in path:
        os.environ["PATH"] = mx_dir+os.pathsep+os.environ["PATH"]
        sys.path.append(mx_dir)


class AutoWrapStaticText(StaticText):
    """
    A simple class derived from :mod:`lib.stattext` that implements auto-wrapping
    behaviour depending on the parent size.
    .. versionadded:: 0.9.5
    Code from: https://github.com/wxWidgets/Phoenix/blob/master/wx/lib/agw/infobar.py
    Original author: Andrea Gavana
    """
    def __init__(self, parent, label):
        """
        Defsult class constructor.
        :param Window parent: a subclass of :class:`Window`, must not be ``None``;
        :param string `label`: the :class:`AutoWrapStaticText` text label.
        """
        StaticText.__init__(self, parent, wx.ID_ANY, label, style=wx.ST_NO_AUTORESIZE)
        self.label = label
        # colBg = wx.SystemSettings.GetColour(wx.SYS_COLOUR_INFOBK)
        # self.SetBackgroundColour(colBg)
        # self.SetOwnForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_INFOTEXT))

        self.Bind(wx.EVT_SIZE, self.OnSize)
        self.Bind(wx.EVT_SPLITTER_SASH_POS_CHANGED, self.OnSize)
        self.Bind(wx.EVT_SPLITTER_SASH_POS_CHANGING, self.OnSize)

    def OnSize(self, event):
        """
        Handles the ``wx.EVT_SIZE`` event for :class:`AutoWrapStaticText`.
        :param `event`: a :class:`SizeEvent` event to be processed.
        """
        event.Skip()
        self.Wrap(event.GetSize().width)

    def Wrap(self, width):
        """
        This functions wraps the controls label so that each of its lines becomes at
        most `width` pixels wide if possible (the lines are broken at words boundaries
        so it might not be the case if words are too long).
        If `width` is negative, no wrapping is done.
        :param integer `width`: the maximum available width for the text, in pixels.
        :note: Note that this `width` is not necessarily the total width of the control,
        since a few pixels for the border (depending on the controls border style) may be added.
        """
        if width < 0:
           return
        self.Freeze()

        dc = wx.ClientDC(self)
        dc.SetFont(self.GetFont())
        text = wordwrap(self.label, width, dc)
        self.SetLabel(text, wrapped=True)

        self.Thaw()

    def SetLabel(self, label, wrapped=False):
        """
        Sets the :class:`AutoWrapStaticText` label.
        All "&" characters in the label are special and indicate that the following character is
        a mnemonic for this control and can be used to activate it from the keyboard (typically
        by using ``Alt`` key in combination with it). To insert a literal ampersand character, you
        need to double it, i.e. use "&&". If this behaviour is undesirable, use `SetLabelText` instead.
        :param string `label`: the new :class:`AutoWrapStaticText` text label;
        :param bool `wrapped`: ``True`` if this method was called by the developer using :meth:`~AutoWrapStaticText.SetLabel`,
        ``False`` if it comes from the :meth:`~AutoWrapStaticText.OnSize` event handler.
        :note: Reimplemented from :class:`PyControl`.
        """

        if not wrapped:
            self.label = label

        StaticText.SetLabel(self, label)

class CustomPlotToolbar(NavigationToolbar2WxAgg):
    """
    A custom plot toolbar that displays the cursor position (or other text)
    in addition to the usual controls.
    """
    def __init__(self, canvas):
        """
        Initializes the toolbar.

        :param wx.Window parent: The parent window
        :param matplotlib.Canvas: The canvas associated with the toolbar.
        """
        NavigationToolbar2WxAgg.__init__(self, canvas)

        self.status = wx.StaticText(self, label='')

        self.AddControl(self.status)

    def set_status(self, status):
        """
        Called to set the status text in the toolbar, i.e. the cursor position
        on the plot.
        """
        self.status.SetLabel(status)


# For XPS driver
# from: https://github.com/pyepics/newportxps utils.py
#

# it appears ftp really wants this encoding:
FTP_ENCODING = 'latin-1'

def bytes2str(s):
    return str(s)


if six.PY3:
    from io import BytesIO as bytesio

    def bytes2str(s):
        'byte to string conversion'
        if isinstance(s, str):
            return s
        elif isinstance(s, bytes):
            return str(s, FTP_ENCODING)
        else:
            return str(s)

class FloatSpinEvent(wx.PyCommandEvent):

    def __init__(self, evtType, id, obj):

        wx.PyCommandEvent.__init__(self, evtType, id)
        self.value = 0
        self.obj = obj

    def GetValue(self):
        return self.value

    def SetValue(self, value):
        self.value = value

    def GetEventObject(self):
        return self.obj

myEVT_MY_SPIN = wx.NewEventType()
EVT_MY_SPIN = wx.PyEventBinder(myEVT_MY_SPIN, 1)

class IntSpinCtrl(wx.Panel):

    def __init__(self, parent, my_id=wx.ID_ANY, my_min=None, my_max=None,
        TextLength=40, **kwargs):

        wx.Panel.__init__(self, parent, my_id, **kwargs)

        if platform.system() != 'Windows':
            self.ScalerButton = wx.SpinButton(self, style=wx.SP_VERTICAL)
        else:
            self.ScalerButton = wx.SpinButton(self, size=self._FromDIP((-1,22)),
                style=wx.SP_VERTICAL)

        self.ScalerButton.Bind(wx.EVT_SET_FOCUS, self.OnScaleChange)
        self.ScalerButton.Bind(wx.EVT_SPIN_UP, self.OnSpinUpScale)
        self.ScalerButton.Bind(wx.EVT_SPIN_DOWN, self.OnSpinDownScale)
        self.ScalerButton.SetRange(-99999, 99999)
        self.max = my_max
        self.min = my_min

        if platform.system() != 'Windows':
            self.Scale = wx.TextCtrl(self, value=str(my_min),
                size=self._FromDIP((TextLength,-1)), style=wx.TE_PROCESS_ENTER,
                validator=CharValidator('int'))
        else:
            self.Scale = wx.TextCtrl(self, value=str(my_min),
                size=self._FromDIP((TextLength,22)), style=wx.TE_PROCESS_ENTER,
                validator=CharValidator('int'))

        self.Scale.Bind(wx.EVT_KILL_FOCUS, self.OnScaleChange)
        self.Scale.Bind(wx.EVT_TEXT_ENTER, self.OnScaleChange)
        self.Scale.Bind(wx.EVT_TEXT, self.OnText)

        sizer = wx.BoxSizer()

        sizer.Add(self.Scale, 0, wx.RIGHT, 1)
        sizer.Add(self.ScalerButton, 0)

        self.oldValue = 0

        self.SetSizer(sizer)

        self.ScalerButton.SetValue(0)

        self.Scale.SetBackgroundColour(wx.NullColour)
        self.Scale.SetModified(False)

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def CastFloatSpinEvent(self):
        event = FloatSpinEvent(myEVT_MY_SPIN, self.GetId(), self)
        event.SetValue(self.Scale.GetValue())
        self.GetEventHandler().ProcessEvent(event)

    def OnText(self, event):
        """
        Called when text is changed in the box. Changes the background
        color of the text box to indicate there are unset changes.
        """
        self.Scale.SetBackgroundColour("yellow")
        self.Scale.SetModified(True)

    def OnScaleChange(self, event):
        self.ScalerButton.SetValue(0) # Resit spinbutton position for button to work in linux

        val = self.Scale.GetValue()

        try:
            float(val)
        except ValueError:
            return

        if self.max is not None:
            if float(val) > self.max:
                self.Scale.ChangeValue(str(self.max))
        if self.min is not None:
            if float(val) < self.min:
                self.Scale.ChangeValue(str(self.min))

        #if val != self.oldValue:
        self.oldValue = val
        self.CastFloatSpinEvent()

        event.Skip()

        self.Scale.SetBackgroundColour(wx.NullColour)
        self.Scale.SetModified(False)

    def OnSpinUpScale(self, event):
        self.ScalerButton.SetFocus()    # Just to remove focus from the bgscaler to throw kill_focus event and update

        val = self.Scale.GetValue()
        try:
            float(val)
        except ValueError:
            if self.min is not None:
                val = self.min -1
            elif self.max is not None:
                val = self.max -1
            else:
                return

        newval = int(val) + 1

        # Reset spinbutton counter. Fixes bug on MAC
        if self.ScalerButton.GetValue() > 90000:
            self.ScalerButton.SetValue(0)

        if self.max is not None:
            if newval > self.max:
                self.Scale.ChangeValue(str(self.max))
            else:
                self.Scale.ChangeValue(str(newval))
        else:
            self.Scale.ChangeValue(str(newval))

        self.oldValue = newval
        wx.CallAfter(self.CastFloatSpinEvent)

        self.Scale.SetBackgroundColour(wx.NullColour)
        self.Scale.SetModified(False)

    def OnSpinDownScale(self, event):
        #self.ScalerButton.SetValue(80)   # This breaks the spinbutton on Linux
        self.ScalerButton.SetFocus()    # Just to remove focus from the bgscaler to throw kill_focus event and update

        val = self.Scale.GetValue()

        try:
            float(val)
        except ValueError:
            if self.max is not None:
                val = self.max +1
            elif self.min is not None:
                val = self.min +1
            else:
                return

        newval = int(val) - 1

        # Reset spinbutton counter. Fixes bug on MAC
        if self.ScalerButton.GetValue() < -90000:
            self.ScalerButton.SetValue(0)

        if self.min is not None:
            if newval < self.min:
                self.Scale.ChangeValue(str(self.min))
            else:
                self.Scale.ChangeValue(str(newval))
        else:
            self.Scale.ChangeValue(str(newval))

        self.oldValue = newval
        wx.CallAfter(self.CastFloatSpinEvent)

        self.Scale.SetBackgroundColour(wx.NullColour)
        self.Scale.SetModified(False)

    def GetValue(self):
        value = self.Scale.GetValue()

        try:
            return int(value)
        except ValueError:
            return value

    def SetValue(self, value):
        self.Scale.SetValue(str(value))

    def ChangeValue(self, value):
        self.Scale.ChangeValue(str(value))

    def SetRange(self, minmax):
        self.max = minmax[1]
        self.min = minmax[0]

    def GetRange(self):
        return (self.min, self.max)

    def SetMin(self, value):
        self.min = int(value)

    def SetMax(self, value):
        self.max = int(value)

    def SafeSetValue(self, val):
        if not self.Scale.IsModified():
            self.SetValue(val)

    def SafeChangeValue(self, val):
        if not self.Scale.IsModified():
            self.ChangeValue(val)

class WarningMessage(wx.Frame):
    def __init__(self, parent, msg, title, callback, *args, **kwargs):
        """
        Initializes the pump frame. Takes args and kwargs for the wx.Frame class.
        """
        super(WarningMessage, self).__init__(parent, *args, title=title, **kwargs)
        logger.debug('Setting up the WarningMessage')

        self.Bind(wx.EVT_CLOSE, self._on_exit)

        self._create_layout(msg)

        self.exit_callback = callback

        self.Layout()
        self.SendSizeEvent()
        self.Fit()
        self.SetSize(self.GetSize()[0], self.GetSize()[1]+30)
        self.Raise()

    def _create_layout(self, msg):
        msg_panel = wx.Panel(self)

        msg_sizer = wx.BoxSizer(wx.HORIZONTAL)
        msg_sizer.Add(wx.StaticBitmap(msg_panel, bitmap=wx.ArtProvider.GetBitmap(wx.ART_WARNING)),
         border=5, flag=wx.RIGHT)
        msg_sizer.Add(AutoWrapStaticText(msg_panel, msg), flag=wx.EXPAND, proportion=1)

        ok_button = wx.Button(msg_panel, label='OK')
        ok_button.Bind(wx.EVT_BUTTON, self._on_exit)

        button_sizer = wx.BoxSizer(wx.VERTICAL)
        button_sizer.Add(ok_button, flag=wx.ALIGN_CENTER_HORIZONTAL)


        panel_sizer = wx.BoxSizer(wx.VERTICAL)
        panel_sizer.Add(msg_sizer, proportion=1, border=5, flag=wx.ALL|wx.EXPAND)
        panel_sizer.Add(button_sizer, border=5, flag=wx.ALL|wx.ALIGN_CENTER_HORIZONTAL)

        msg_panel.SetSizer(panel_sizer)

        top_sizer = wx.BoxSizer(wx.HORIZONTAL)
        top_sizer.Add(msg_panel, flag=wx.EXPAND, proportion=1)

        self.SetSizer(top_sizer)

    def _on_exit(self, evt):
        self.exit_callback()

        self.Destroy()

class ValueEntry(wx.TextCtrl):
    def __init__(self, enter_callback, *args, **kwargs):
        wx.TextCtrl.__init__(self, *args, style=wx.TE_PROCESS_ENTER, **kwargs)

        self._enter_callback = enter_callback

        self.Bind(wx.EVT_TEXT, self.OnText)

        self.Bind(wx.EVT_TEXT_ENTER, self.OnEnter)

    def OnText(self, event):
        """
        Called when text is changed in the box. Changes the background
        color of the text box to indicate there are unset changes.
        """
        self.SetBackgroundColour("yellow")
        self.SetModified(True)

    def OnEnter(self, event):
        """
        When enter is pressed in the box, it sets the value in EPICS.
        """
        value = self.GetValue().strip()
        self._enter_callback(self, value)
        self.SetBackgroundColour(wx.NullColour)
        self.SetModified(False)

    def SafeSetValue(self, val):
        if not self.IsModified():
            self.SetValue(val)

    def SafeChangeValue(self, val):
        if not self.IsModified():
            self.ChangeValue(val)

def find_closest(val, array):
    argmin = np.argmin(np.absolute(array-val))

    return array[argmin], argmin


class CommManager(threading.Thread):
    def __init__(self, name=None):
        """
        Initializes the custom thread.

        :param collections.deque command_queue: The queue used to pass commands
            to the thread.

        :param collections.deque return_queue: The queue used to return data
            from the thread.

        :param threading.Event abort_event: An event that is set when the thread
            needs to abort, and otherwise is not set.
        """
        threading.Thread.__init__(self, name=name)
        self.daemon = True

        logger.info("Starting communication thread: %s", self.name)

        self._command_queues = {}
        self._return_queues = {}
        self._status_queues = {}
        self._abort_event = threading.Event()
        self._stop_event = threading.Event()
        self._queue_lock = threading.Lock()

        self._status_cmds = {}

        self._commands = {'example_command' : self._example_command} # overwrite

        self._connected_devices = OrderedDict()
        self._connected_coms = OrderedDict()

        # Need to make run and abort work for multiple queues
        # Need to add a way to set status commands and intervals
        # Need to add a way to add on stop commands?

    def run(self):
        """
        Custom run method for the thread.
        """
        while True:
            cmds_run = False
            with self._queue_lock:
                for comm_name, cmd_q in self._command_queues.items():
                    if len(cmd_q) > 0:
                        logger.debug("Getting new command")
                        command, args, kwargs = cmd_q.popleft()
                    else:
                        command = None

                    if self._abort_event.is_set():
                        break

                    if self._stop_event.is_set():
                        break

                    if command is not None:
                        kwargs['comm_name'] = comm_name
                        kwargs['cmd'] = command
                        self._run_command(command, args, kwargs)

                        cmds_run = True

            if self._abort_event.is_set():
                logger.debug("Abort event detected")
                self._abort()

            if self._stop_event.is_set():
                logger.debug("Stop event detected")
                break

            with self._queue_lock:
                for status_cmd in self._status_cmds:
                    temp = self._status_cmds[status_cmd]
                    cmd, args, kwargs = temp['cmd']
                    period = temp['period']
                    last_t = temp['last_run']

                    if self._abort_event.is_set():
                        break

                    if self._stop_event.is_set():
                        break

                    if time.time() - last_t > period:
                        kwargs['comm_name'] = 'status'
                        kwargs['cmd'] = cmd
                        self._run_command(cmd, args, kwargs)
                        self._status_cmds[status_cmd]['last_run'] = time.time()

                        cmds_run = True

            if self._abort_event.is_set():
                logger.debug("Abort event detected")
                self._abort()

            if self._stop_event.is_set():
                logger.debug("Stop event detected")
                self._abort()
                break

            if not cmds_run:
                time.sleep(0.01)

        if self._stop_event.is_set():
            self._stop_event.clear()
        else:
            self._abort()

        self._cleanup_devices()

        logger.info("Quitting communication thread: %s", self.name)

    def _run_command(self, command, args, kwargs):
        logger.debug(("Processing cmd '%s' with args: %s and "
            "kwargs: %s "), command, ', '.join(['{}'.format(a) for a in args]),
            ', '.join(['{}:{}'.format(kw, item) for kw, item in kwargs.items()]))

        try:
            self._commands[command](*args, **kwargs)
        except Exception:
            msg = ("Communication thread %s failed to run command '%s' "
                "with args: %s and kwargs: %s " %(self.name, command,
                ', '.join(['{}'.format(a) for a in args]),
                ', '.join(['{}:{}'.format(kw, item) for kw, item in kwargs.items()])))
            logger.exception(msg)

            self._return_value((command, False), kwargs['comm_name'])

    def add_new_communication(self, name, command_queue, return_queue, status_queue):
        logger.info('Adding new communication device to thread: %s', name)
        with self._queue_lock:
            self._command_queues[name] = command_queue
            self._return_queues[name] = return_queue
            self._status_queues[name] = status_queue

            self._additional_new_comm(name)

        logger.debug('Added new communication device to thread')

    def _additional_new_comm(self, name):
        pass #Add device specific stuff here

    def remove_communication(self, name):
        logger.info('Removing communication device from thread: %s', name)
        with self._queue_lock:
            self._command_queues.pop(name, None)
            self._return_queues.pop(name, None)
            self._status_queues.pop(name, None)

        logger.info('Removed communication device from thread')

    def add_status_cmd(self, cmd, period):
        logger.debug('Adding status command: %s', cmd)
        with self._queue_lock:
            cmd_key = '{}_{}'.format(cmd[0], cmd[1][0])
            self._status_cmds[cmd_key] = {'cmd' : cmd, 'period' : period, 'last_run': 0}

        logger.debug('Added status command')

    def remove_status_cmd(self, cmd):
        logger.debug('Removing status command: %s', cmd)

        with self._queue_lock:
            self._status_cmds.pop(cmd[0], None)

        logger.debug('Removed status command')

    def _example_command(self, name, **kwargs):
        """
        Commands need to take in 0 or more arguments, 0 or more key word agruments,
        and additionally must always take in the comm_name keyword argument, which
        is used to make sure the right return queue is used.
        """
        comm_name = kwargs.pop('comm_name', None)
        self._return_value((name, 'example_command', None), comm_name)
        pass

    def _connect_device(self, name, device_type, device, **kwargs):
        logger.info("Connecting device %s", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        if name not in self._connected_devices:
            # if device is None or device not in self._connected_coms:
            #     new_device = self.known_devices[device_type](name, device, **kwargs)
            #     new_device.connect()
            #     self._connected_devices[name] = new_device
            #     self._connected_coms[device] = new_device
            #     logger.debug("Device %s connected", name)
            # else:
            #     self._connected_devices[name] = self._connected_coms[device]
            #     logger.debug("Device already connected on %s", device)

            new_device = self.known_devices[device_type](name, device, **kwargs)
            new_device.connect()
            self._connected_devices[name] = new_device
            self._connected_coms[device] = new_device
            logger.debug("Device %s connected", name)

            self._additional_connect_device(name, device_type, device, **kwargs)

        self._return_value((name, cmd, True), comm_name)

    def _additional_connect_device(self, name, device_type, device, **kwargs):
        pass # Device specific stuff here if needed

    def _disconnect_device(self, name, **kwargs):
        logger.info("Disconnecting device %s", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices.pop(name, None)
        if device is not None:
            device.disconnect()

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s disconnected", name)

    def _return_value(self, val, comm_name):
        if comm_name == 'status':
            return_queue_list = self._status_queues.values()
        elif comm_name is not None:
            return_queue_list = [self._return_queues[comm_name]]
        else:
            return_queue_list = []

        for ret_q in return_queue_list:
            ret_q.append(val)

    def abort(self):
        self._abort_event.set()

    def _abort(self):
        """
        Clears the ``command_queue`` and the ``return_queue``.
        """
        logger.info("Aborting communication thread %s current and future commands", self.name)
        with self._queue_lock:
            for comm_name, cmd_q in self._command_queues.items():
                cmd_q.clear()

            for comm_name, ret_q in self._return_queues.items():
                ret_q.clear()

            for comm_name, status_q in self._status_queues.items():
                status_q.clear()

            self._additional_abort()

        self._abort_event.clear()
        logger.debug("Communication thread %s aborted", self.name)

    def _additional_abort(self):
        pass #Device specific stuff here

    def stop(self):
        """Stops the thread cleanly."""
        logger.info("Starting to clean up and shut down communication thread: %s", self.name)
        self._stop_event.set()

    def _cleanup_devices(self):
        pass #Set for each device type

class DevicePanel(wx.Panel):
    """
    This device panel supports standard settings, including connection settings,
    for a device. It is meant to be embedded in a larger application and can
    be instanced several times, once for each device. It communciates
    with the devices using the :py:class:`CommManager`.
    """
    def __init__(self, parent, panel_id, settings, *args, **kwargs):
        """
        :param wx.Window parent: Parent class for the panel.

        :param int panel_id: wx ID for the panel.

        :param str panel_name: Name for the panel

        :param com_thread: The communication thread for the device.

        :param dict device_data" A dictionary containing at least the keys
        name, args, kwargs for the device.

        """
        self.settings = settings
        self.remote = settings['remote']

        self.name = settings['device_data']['name']
        self.parent = parent

        if 'name' not in kwargs:
            kwargs['name'] = self.name

        wx.Panel.__init__(self, parent, panel_id, *args, **kwargs)

        logger.debug('Initializing DevicePanel for device %s', self.name)

        self.connected = False

        self.cmd_q = deque()
        self.return_q = deque()
        self.status_q = deque()

        if not self.remote:
            self.com_thread = settings['com_thread']

            self.com_timeout_event = None
            self.remote_dev = None

            if self.com_thread is not None:
                self.com_thread.add_new_communication(self.name, self.cmd_q, self.return_q,
                    self.status_q)

        else:
            self.com_abort_event = threading.Event()
            self.com_timeout_event = threading.Event()

            ip = settings['remote_ip']
            port = settings['remote_port']
            self.remote_dev = settings['remote_device']

            self.com_thread = client.ControlClient(ip, port, self.cmd_q,
                self.return_q, self.com_abort_event, self.com_timeout_event,
                name='{}_ControlClient'.format(self.remote_dev),
                status_queue=self.status_q)

            self.com_thread.start()

        self._clear_return = threading.Lock()
        self._stop_status = threading.Event()

        self._create_layout()
        self._init_device(settings)

        # Dictionary of status settings that should be defined. If a status
        # response is returned, the command name is used as a key and the
        # function returned is run on the return value
        self._status_settings = {}
        self._status_thread = threading.Thread(target=self._get_status)
        self._status_thread.daemon = True
        self._status_thread.start()

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _create_layout(self):
        """Creates the layout for the panel."""

        top_sizer = wx.BoxSizer(wx.VERTICAL)

        self.SetSizer(top_sizer)

    def _init_device(self, settings):
        """
        Initializes the device parameters if any were provided. If enough are
        provided the device is automatically connected.
        """
        device_data = settings['device_data']

    def _update_status_cmd(self, cmd, status_period, add_status=True):
        if self.remote:
            self._send_cmd(cmd, is_status=True, status_period=status_period,
                add_status=add_status)

        else:
            if add_status:
                self.com_thread.add_status_cmd(cmd, status_period)
            else:
                self.com_thread.remove_status_cmd(cmd)

    def _send_cmd(self, cmd, get_response=False, is_status=False,
        status_period=1, add_status=True):
        """
        Sends commands to the pump using the ``cmd_q`` that was given
        to :py:class:`UVCommThread`.

        :param str cmd: The command to send, matching the command in the
            :py:class:`UVCommThread` ``_commands`` dictionary.
        """
        logger.debug('Sending device %s command %s', self.name, cmd)

        ret_val = send_cmd(cmd, self.cmd_q, self.return_q, self.com_timeout_event,
            self._clear_return, self.remote, self.remote_dev,
            get_response=get_response, is_status=is_status,
            status_period=status_period, add_status=add_status)

        return ret_val

    def _get_status(self):
        while not self._stop_status.is_set():
            if len(self.status_q) > 0:
                new_status = self.status_q.popleft()

            else:
                new_status = None

            if new_status is not None:
                try:
                    device, cmd, val = new_status
                except Exception:
                    device = None

                if device == self.name:
                    wx.CallAfter(self._set_status, cmd, val)

            else:
                time.sleep(0.01)

            with self._clear_return:
                while len(self.return_q) > 0:
                    self.return_q.pop()

    def _set_status(self, cmd, val):
        pass # Overwrite this

    def close(self):
        self._on_close()

        if not self.remote:
            if self.com_thread is not None:
                self.com_thread.remove_communication(self.name)
        else:
            self.com_thread.stop()

            if not self.com_timeout_event.is_set():
                self.com_thread.join(5)

        self._stop_status.set()
        self._status_thread.join()

    def _on_close(self):
        """Device specific stuff goes here"""
        pass

def send_cmd(cmd, cmd_q, return_q, timeout_event, return_lock, remote,
    remote_dev, get_response=False, is_status=False, status_period=1,
    add_status=True):

    if remote:
        if is_status:
            device = '{}_status'.format(remote_dev)
            cmd = [cmd, status_period, add_status]
        else:
            device = '{}'.format(remote_dev)

        full_cmd = {'device': device, 'command': cmd, 'response': get_response}

    else:
        full_cmd = cmd

    if not remote:
        with return_lock:
            cmd_q.append(full_cmd)
            result = wait_for_response(return_q, timeout_event, remote)

    else:
        if get_response:
            with return_lock:
                cmd_q.append(full_cmd)
                result = wait_for_response(return_q, timeout_event, remote)

        else:
            cmd_q.append(full_cmd)


    if get_response:
        if result is not None and result[0] == cmd[1][0] and result[1] == cmd[0]:
            ret_val = result[2]
        else:
            ret_val = None
    else:
        ret_val = None

    return ret_val

def wait_for_response(return_q, timeout_event, remote):
    start_count = len(return_q)
    while len(return_q) == start_count:
        time.sleep(0.01)

        if remote and timeout_event.is_set():
            break

    if remote:
        if not timeout_event.is_set():
            answer = return_q.pop()
        else:
            answer = None
    else:
        answer = return_q.pop()

    return answer

class DeviceFrame(wx.Frame):
    """
    A lightweight frame allowing one to work with arbitrary number of devices.
    Only meant to be used when the device module is run directly,
    rather than when it is imported into another program.
    """
    def __init__(self, name, settings, device_panel, *args, **kwargs):
        """
        Initializes the device frame. Takes frame name, utils.CommManager thread
        (or subclass), the device_panel class, and args and kwargs for the wx.Frame class.
        """
        super(DeviceFrame, self).__init__(*args, **kwargs)

        self.name = name
        self.device_panel = device_panel

        logger.debug('Setting up the DeviceFrame %s', self.name)

        self.settings = settings

        self.Bind(wx.EVT_CLOSE, self._on_exit)

        self.devices =[]

        self._create_layout()

        self.Fit()
        self.Raise()

        # Enable these to init devices on startup
        # self.setup_devices = []
        # self._init_devices()

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size


    def _create_layout(self):
        """Creates the layout"""

        #Overwrite this
        self.sizer = wx.BoxSizer(wx.HORIZONTAL)

        device_sizer = wx.BoxSizer(wx.VERTICAL)
        device_sizer.Add(self.sizer, 1, flag=wx.EXPAND)

        self.device_parent = wx.Panel(self)

        self.device_parent.SetSizer(device_sizer)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(self.device_parent, 1, flag=wx.EXPAND)

        self.SetSizer(top_sizer)

    def _init_devices(self):
        """
        This is a convenience function for initalizing devices on startup, if you
        already know what devices you want to add. You can add/comment it out in
        the ``__init__`` if you want to not load any devices on startup.

        If you want to add devices here, add them to the ``setup_devices`` list.
        Each entry should be an iterable with the following parameters: name,
        device type, comport, arg list, and kwarg dict in that order. How the
        arg list and kwarg dict are handled are defined in the
        DevicePanel._init_devices function, and depends on the device type.

        Add this to the _init__ and add a self.setup_devices list to the init
        """
        if not self.devices:
            try:
                self.sizer.Remove(0)
            except Exception:
                pass

        logger.info('Initializing %s devices on startup', str(len(self.setup_devices)))

        if self.setup_devices is not None:
            for device in self.setup_devices:
                dev_settings = {}
                for key, val in self.settings.items():
                    if key != 'com_thread':
                        dev_settings[key] = copy.deepcopy(val)
                    else:
                        dev_settings[key] = val

                dev_settings['device_data'] = device
                new_device = self.device_panel(self.device_parent, wx.ID_ANY,
                    dev_settings)

                self.sizer.Add(new_device, 1, flag=wx.EXPAND|wx.ALL,
                    border=self._FromDIP(3))
                self.devices.append(new_device)

        self.Layout()
        self.Fit()

    def _on_add_device(self, evt):
        """
        Called when the Add Devices button is used. Adds a new device
        to the control panel.

        .. note:: device names must be distinct.
        """
        if not self.devices:
            self.sizer.Remove(0)

        dlg = wx.TextEntryDialog(self, "Enter device name:", "Create new device")
        if dlg.ShowModal() == wx.ID_OK:
            name = dlg.GetValue()
            for device in self.devices:
                if name == device.name:
                    msg = "device names must be distinct. Please choose a different name."
                    wx.MessageBox(msg, "Failed to add device")
                    logger.debug('Attempted to add a device with the same name (%s) as another pump.', name)
                    return

            new_device = self.device_panel(self, wx.ID_ANY, name, self.ports, self.cmd_q,
                self.return_q, name)
            logger.info('Added new device %s to the device control panel.', name)
            self.sizer.Add(new_device)
            self.devices.append(new_device)

            self.Layout()
            self.Fit()

        return

    def _on_exit(self, evt):
        """
        Removes communication to the device. You still need to close the device
        elsewhere in the program.
        """
        logger.debug('Closing the DeviceFrame')
        for device in self.devices:
            device.close()

        self.Destroy()


class ItemList(wx.Panel):
    def __init__(self, *args, **kwargs):
        wx.Panel.__init__(self, *args, **kwargs)

        self._create_layout()

        self.all_items = []
        self.selected_items = []
        self.modified_items = []
        self._marked_item = None

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _create_layout(self):
        self.list_panel = wx.ScrolledWindow(self, style=wx.BORDER_SUNKEN)
        self.list_panel.SetScrollRate(20,20)

        self.list_bkg_color = wx.SystemSettings.GetColour(wx.SYS_COLOUR_LISTBOX)

        self.list_panel.SetBackgroundColour(self.list_bkg_color)

        self.list_panel_sizer = wx.BoxSizer(wx.VERTICAL)
        self.list_panel.SetSizer(self.list_panel_sizer)

        toolbar_sizer = self._create_toolbar()
        button_sizer = self._create_buttons()

        panel_sizer = wx.BoxSizer(wx.VERTICAL)
        if toolbar_sizer is not None:
            panel_sizer.Add(toolbar_sizer, border=self._FromDIP(5), flag=wx.LEFT
                |wx.RIGHT|wx.EXPAND)
        panel_sizer.Add(self.list_panel, proportion=1, border=self._FromDIP(3),
            flag=wx.TOP|wx.LEFT|wx.RIGHT|wx.EXPAND)
        if button_sizer is not None:
            panel_sizer.Add(button_sizer, border=self._FromDIP(10),
                flag=wx.EXPAND|wx.ALL)

        self.SetSizer(panel_sizer)

    def updateColors(self):
        self.list_panel.SetBackgroundColour(self.list_bkg_color)

        for item in self.all_items:
            item.updateColors()
        self.Refresh()

    def _create_toolbar(self):
        return None

    def _create_buttons(self):
        return None

    def create_items(self):
        pass

    def resize_list(self):
        self.list_panel.SetVirtualSize(self.list_panel.GetBestVirtualSize())
        self.list_panel.Layout()
        self.list_panel.Refresh()

    def add_items(self, items):
        for item in items:
            self.list_panel_sizer.Add(item, flag=wx.EXPAND|wx.ALL,
                border=self._FromDIP(1))
            self.all_items.append(item)

        self.resize_list()

    def mark_item(self, item):
        self._marked_item = item

    def get_marked_item(self):
        return self._marked_item

    def clear_marked_item(self):
        self._marked_item = None

    def clear_list(self):
        self._marked_item = None
        self.selected_items = []
        self.modified_items = []

        remaining_items = []

        for item in self.all_items:
            try:
                item.Destroy()
            except Exception:
                remaining_items.append(item)

        self.all_items = remaining_items

        self.resize_list()

    def get_selected_items(self):
        self.selected_items = []

        for item in self.all_items:
            if item.get_selected():
                self.selected_items.append(item)

        return self.selected_items

    def select_all(self):
        for item in self.all_items:
            item.set_selected(True)

    def deselect_all_but_one(self, sel_item):
        selected_items = self.get_selected_items()

        for item in selected_items:
            if item is not sel_item:
                item.set_selected(False)

    def select_to_item(self, sel_item):
        selected_items = self.get_selected_items()

        sel_idx = self.get_item_index(sel_item)

        first_idx = self.get_item_index(selected_items[0])

        if sel_item in selected_items:
            for item in self.all_items[first_idx:sel_idx]:
                item.set_selected(False)
        else:
            if sel_idx < first_idx:
                for item in self.all_items[sel_idx:first_idx]:
                    item.set_selected(True)
            else:
                last_idx = self.get_item_index(selected_items[-1])
                for item in self.all_items[last_idx+1:sel_idx+1]:
                    item.set_selected(True)

    def remove_items(self, items):
        for item in items:
            self.remove_item(item, resize=False)

        self.resize_list()

    def remove_selected_items(self):
        selected_items = self.get_selected_items()

        if len(selected_items) > 0:
            self.remove_items(selected_items)

    def remove_item(self, item, resize=True):
        item.remove()

        if item in self.modified_items:
            self.modified_items.remove(item)

        if item in self.selected_items:
            self.selected_items.remove(item)

        self.all_items.remove(item)

        item.Destroy()

        self.resize_list()

    def get_items(self):
        return self.all_items

    def get_item_index(self, item):
        return self.all_items.index(item)

    def get_item(self, index):
        return self.all_items[index]

    def move_item(self, item, move_dir, refresh=True):
        item_idx = self.get_item_index(item)

        if move_dir == 'up' and item_idx > 0:
            new_item_idx = item_idx -1

        elif move_dir == 'down' and item_idx < len(self.all_items) -1:
            new_item_idx = item_idx +1

        else:
            new_item_idx = -1

        if new_item_idx != -1:
            self.list_panel_sizer.Detach(item)
            self.list_panel_sizer.Insert(new_item_idx, item, flag=wx.EXPAND|wx.ALL,
                border=self._FromDIP(1))

            self.all_items.pop(item_idx)
            self.all_items.insert(new_item_idx, item)

            if refresh:
                self.resize_list()


class ListItem(wx.Panel):
    def __init__(self, item_list, *args, **kwargs):
        wx.Panel.__init__(self, *args, parent=item_list.list_panel,
            style=wx.BORDER_RAISED, **kwargs)

        self._selected = False

        self.item_list = item_list

        self.text_list = []

        self._create_layout()

        self.Bind(wx.EVT_LEFT_DOWN, self._on_left_mouse_btn)
        self.Bind(wx.EVT_RIGHT_DOWN, self._on_right_mouse_btn)
        self.Bind(wx.EVT_KEY_DOWN, self._on_key_press)

        self.general_text_color = wx.SystemSettings.GetColour(wx.SYS_COLOUR_LISTBOXTEXT)
        self.highlight_text_color = wx.SystemSettings.GetColour(wx.SYS_COLOUR_LISTBOXHIGHLIGHTTEXT)
        self.list_bkg_color = wx.SystemSettings.GetColour(wx.SYS_COLOUR_LISTBOX)
        self.highlight_list_bkg_color = wx.Colour(178, 215, 255)

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _create_layout(self):
        pass

    def updateColors(self):
        self.set_selected(self._selected)

    def get_selected(self):
        return self._selected

    def set_selected(self, selected):
        self._selected = selected

        if self._selected:

            self.SetBackgroundColour(self.highlight_list_bkg_color)

            for text_item in self.text_list:
                text_item.SetForegroundColour(self.general_text_color)

        else:
            self.SetBackgroundColour(self.list_bkg_color)
            for text_item in self.text_list:
                text_item.SetForegroundColour(self.general_text_color)

        self.Refresh()

    def toggle_selected(self):
        self.set_selected(not self._selected)

    def remove(self):
        pass

    def _on_left_mouse_btn(self, event):
        if self.IsEnabled():
            ctrl_is_down = event.CmdDown()
            shift_is_down = event.ShiftDown()

            if shift_is_down:
                self.item_list.select_to_item(self)
            elif ctrl_is_down:
                self.toggle_selected()
            else:
                self.item_list.deselect_all_but_one(self)
                self.toggle_selected()

    def _on_right_mouse_btn(self, event):
        pass

    def _on_key_press(self, event):
        pass


elveflow_errors = {
    -8000   : 'No digital sensor found',
    -8001   : 'No pressure sensor compatible with OB1 MK3',
    -8002   : 'No digital pressure sensor compatible with OB1 MK3+',
    -8003   : 'No digital flow sensor compatible with OB1 MK3',
    -8004   : 'No IPA config for this sensor',
    -8005   : 'Sensor not compatible with AF1',
    -8006   : 'No instrument with selected ID',
    }


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
            buffers = copy.deepcopy(self._buffers)
        return buffers

    def remove_buffer(self, position):
        """
        Removes the buffer. If the buffer is the active buffer, active buffer
        position is set to None.

        Parameters
        position: str
            The buffer position (e.g. 1 or A or etc)
        """
        with self._buffer_lock:
            position = str(position)
            if position in self._buffers:
                del self._buffers[position]

            if position == self._active_buffer_position:
                self._active_buffer_position = None
                self._previous_flow_rate = None

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


class BufferEntryDialog(wx.Dialog):
    """
    Allows addition/editing of the buffer info in the buffer list
    """
    def __init__(self, parent, buffer_settings, *args, **kwargs):
        wx.Dialog.__init__(self, parent, *args,
            style=wx.RESIZE_BORDER|wx.CAPTION|wx.CLOSE_BOX, **kwargs)

        self.SetSize(self._FromDIP((400, 200)))

        self._buffer_settings = buffer_settings

        self._create_layout()

        self.CenterOnParent()

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _create_layout(self):
        parent = self
        self._buffer_ctrl = wx.Choice(parent, choices=[str(x) for x in range(1,11)])
        self._buffer_ctrl.SetSelection(0)
        self._buffer_ctrl.Bind(wx.EVT_CHOICE, self._on_buffer_choice)

        self._buffer_volume = wx.TextCtrl(parent, size=self._FromDIP((100,-1)),
            validator=utils.CharValidator('float'))
        self._buffer_contents = wx.TextCtrl(parent,
            style=wx.TE_MULTILINE|wx.TE_BESTWRAP)

        buffer_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        buffer_sizer.Add(wx.StaticText(parent, label='Buffer position:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        buffer_sizer.Add(self._buffer_ctrl)
        buffer_sizer.Add(wx.StaticText(parent, label='Buffer volume (L):'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        buffer_sizer.Add(self._buffer_volume)
        buffer_sizer.Add(wx.StaticText(parent, label='Buffer contents:'),
            flag=wx.ALIGN_TOP)
        buffer_sizer.Add(self._buffer_contents, flag=wx.EXPAND)

        buffer_sizer.AddGrowableRow(2)
        buffer_sizer.AddGrowableCol(1)


        button_sizer = self.CreateButtonSizer(wx.OK | wx.CANCEL)

        top_sizer=wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(buffer_sizer, proportion=1, flag=wx.ALL|wx.EXPAND,
            border=self._FromDIP(5))
        top_sizer.Add(button_sizer ,flag=wx.BOTTOM|wx.RIGHT|wx.LEFT|wx.ALIGN_RIGHT,
            border=self._FromDIP(10))

        self.SetSizer(top_sizer)

        self._on_buffer_choice(None)

    def _on_buffer_choice(self, evt):
        pos = self._buffer_ctrl.GetStringSelection()

        if pos in self._buffer_settings:
            vol = self._buffer_settings[pos]['vol']
            descrip = self._buffer_settings[pos]['descrip']

            vol = round(vol/1000., 4)

            self._buffer_volume.SetValue(str(vol))
            self._buffer_contents.SetValue(descrip)

    def get_settings(self):
        pos = self._buffer_ctrl.GetStringSelection()
        vol = self._buffer_volume.GetValue()
        descrip = self._buffer_contents.GetValue()

        return pos, vol, descrip


class BufferList(wx.ListCtrl, wx.lib.mixins.listctrl.ListCtrlAutoWidthMixin):

    def __init__(self, *args, **kwargs):
        wx.ListCtrl.__init__(self, *args, **kwargs)
        self.InsertColumn(0, 'Port')
        self.InsertColumn(1, 'Vol. (L)')
        self.InsertColumn(2, 'Buffer')

        self.SetColumnWidth(0, 40)
        self.SetColumnWidth(1, 50)

        wx.lib.mixins.listctrl.ListCtrlAutoWidthMixin.__init__(self)
