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
from collections import deque
import time

logger = logging.getLogger(__name__)

import wx
from wx.lib.wordwrap import wordwrap
from wx.lib.stattext import GenStaticText as StaticText
from matplotlib.backends.backend_wxagg import NavigationToolbar2WxAgg
import numpy as np

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

    def __init__(self, evtType, id, object):

        wx.PyCommandEvent.__init__(self, evtType, id)
        self.value = 0
        self.object = object

    def GetValue(self):
        return self.value

    def SetValue(self, value):
        self.value = value

    def GetEventObject(self):
        return self.object

myEVT_MY_SPIN = wx.NewEventType()
EVT_MY_SPIN = wx.PyEventBinder(myEVT_MY_SPIN, 1)

class IntSpinCtrl(wx.Panel):

    def __init__(self, parent, id=wx.ID_ANY, min = None, max = None, TextLength = 40, **kwargs):

        wx.Panel.__init__(self, parent, id, **kwargs)

        if platform.system() != 'Windows':
            self.ScalerButton = wx.SpinButton(self, -1, style = wx.SP_VERTICAL)
        else:
            self.ScalerButton = wx.SpinButton(self, -1, size=(-1,22), style = wx.SP_VERTICAL)

        self.ScalerButton.Bind(wx.EVT_SET_FOCUS, self.OnScaleChange)
        self.ScalerButton.Bind(wx.EVT_SPIN_UP, self.OnSpinUpScale)
        self.ScalerButton.Bind(wx.EVT_SPIN_DOWN, self.OnSpinDownScale)
        self.ScalerButton.SetRange(-99999, 99999)
        self.max = max
        self.min = min

        if platform.system() != 'Windows':
            self.Scale = wx.TextCtrl(self, -1, str(min), size = (TextLength,-1), style = wx.TE_PROCESS_ENTER)
        else:
            self.Scale = wx.TextCtrl(self, -1, str(min), size = (TextLength,22), style = wx.TE_PROCESS_ENTER)

        self.Scale.Bind(wx.EVT_KILL_FOCUS, self.OnScaleChange)
        self.Scale.Bind(wx.EVT_TEXT_ENTER, self.OnScaleChange)

        sizer = wx.BoxSizer()

        sizer.Add(self.Scale, 0, wx.RIGHT, 1)
        sizer.Add(self.ScalerButton, 0)

        self.oldValue = 0

        self.SetSizer(sizer)

        self.ScalerButton.SetValue(0)

    def CastFloatSpinEvent(self):
        event = FloatSpinEvent(myEVT_MY_SPIN, self.GetId(), self)
        event.SetValue( self.Scale.GetValue() )
        self.GetEventHandler().ProcessEvent(event)

    def OnScaleChange(self, event):
        self.ScalerButton.SetValue(0) # Resit spinbutton position for button to work in linux

        val = self.Scale.GetValue()

        try:
            float(val)
        except ValueError:
            return

        if self.max is not None:
            if float(val) > self.max:
                self.Scale.SetValue(str(self.max))
        if self.min is not None:
            if float(val) < self.min:
                self.Scale.SetValue(str(self.min))

        #if val != self.oldValue:
        self.oldValue = val
        self.CastFloatSpinEvent()

        event.Skip()

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

        #print self.min, self.max, val, self.ScalerButton.GetMax(), self.ScalerButton.GetValue()

        if self.max is not None:
            if newval > self.max:
                self.Scale.SetValue(str(self.max))
            else:
                self.Scale.SetValue(str(newval))
        else:
            self.Scale.SetValue(str(newval))

        self.oldValue = newval
        wx.CallAfter(self.CastFloatSpinEvent)

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
                self.Scale.SetValue(str(self.min))
            else:
                self.Scale.SetValue(str(newval))
        else:
            self.Scale.SetValue(str(newval))

        self.oldValue = newval
        wx.CallAfter(self.CastFloatSpinEvent)


    def GetValue(self):
        value = self.Scale.GetValue()

        try:
            return int(value)
        except ValueError:
            return value

    def SetValue(self, value):
        self.Scale.SetValue(str(value))

    def SetRange(self, minmax):
        self.max = minmax[1]
        self.min = minmax[0]

    def GetRange(self):
        return (self.min, self.max)

    def SetMin(self, value):
        self.min = value

    def SetMax(self, value):
        self.max = value

class WarningMessage(wx.Frame):
    def __init__(self, parent, msg, title, *args, **kwargs):
        """
        Initializes the pump frame. Takes args and kwargs for the wx.Frame class.
        """
        super(WarningMessage, self).__init__(parent, *args, title=title, **kwargs)
        logger.debug('Setting up the WarningMessage')

        self.Bind(wx.EVT_CLOSE, self._on_exit)

        self._create_layout(msg)

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
        parent=self.GetParent()
        parent.warning_dialog = None

        self.Destroy()

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
                        self._run_command(command, args, kwargs)

                        cmds_run = True

            if self._abort_event.is_set():
                logger.debug("Abort event detected")
                self._abort()

            if self._stop_event.is_set():
                logger.debug("Stop event detected")
                self._abort()
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

            if command == 'connect' or command == 'disconnect':
                self._return_value((command, False), kwargs['comm_name'])

    def add_new_communication(self, name, command_queue, return_queue, status_queue):
        logger.info('Adding new communication device to thread: %s', name)
        with self._queue_lock:
            self._command_queues[name] = command_queue
            self._return_queues[name] = return_queue
            self._status_queues[name] = status_queue

        logger.debug('Added new communication device to thread')

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
            self._status_cmds[cmd[0]] = {'cmd' : cmd, 'period' : period, 'last_run': 0}

        logger.debug('Added status command')

    def remove_status_cmd(self, cmd):
        logger.debug('Removing status command: %s', cmd)

        with self._queue_lock:
            self._status_cmds.pop(cmd, None)

        logger.debug('Removed status command')

    def _example_command(self, comm_name=None):
        """
        Commands need to take in 0 or more arguments, 0 or more key word agruments,
        and additionally must always take in the comm_name keyword argument, which
        is used to make sure the right return queue is used.
        """
        if comm_name == 'status':
            return_queue_list = self._status_queues.values()
        else:
            return_queue_list = [self._return_queues[comm_name]]

    def _return_value(self, val, comm_name):
        if comm_name == 'status':
            return_queue_list = self._status_queues.values()
        else:
            return_queue_list = [self._return_queues[comm_name]]

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

        self._abort_event.clear()
        logger.debug("Communication thread %s aborted", self.name)

    def stop(self):
        """Stops the thread cleanly."""
        logger.info("Starting to clean up and shut down communication thread: %s", self.name)
        self._stop_event.set()
