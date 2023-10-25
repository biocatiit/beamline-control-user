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

import utils


class Automator(threading.Thread):
    def __init__(self, name=None):
        """
        Initializes the custom thread.
        """
        threading.Thread.__init__(self, name=name)
        self.daemon = True

        logger.info("Starting automator thread: %s", self.name)

        self._auto_cons = {} #Dictionary of things controlled by automator
        self._state = 'run' #state of the automator

        self._auto_con_lock = threading.RLock()
        self._state_lock = threading.Lock()


        self._abort_event = threading.Event()
        self._stop_event = threading.Event()

    def run(self):
        """
        Custom run method for the thread.
        """
        while True:
            if self._abort_event.is_set():
                logger.debug("Abort event detected")
                self._abort()

            if self._stop_event.is_set():
                logger.debug("Stop event detected")
                break

            status_change = False

            with self._state_lock:
                if self._state == 'run':
                    run_cmds = True
                else:
                    run_cmds = False

            if run_cmds:
                with self._auto_con_lock:
                    for name, controls in self._auto_cons.items():
                        state = controls['status']['state']

                        if state == 'run' or state == 'equil':
                            self._check_status(name)

                        elif state == 'wait':
                            self._check_wait(name)

                        elif state == 'idle':
                            with controls['cmd_lock']:
                                num_cmds = len(controls['cmd_queue'])

                            if num_cmds > 0:
                                self._run_next_cmd(name)
                                status_change = True

            if not status_change:
                time.sleep(0.5)

        if self._stop_event.is_set():
            self._stop_event.clear()
        else:
            self._abort()

        logger.info("Quitting automator thread: %s", self.name)

    def _check_status(self, name):
        with self._auto_con_lock:
            controls = self._auto_cons[name]

            with controls['cmd_lock']:
                cmd_func = controls['cmd_func']


                cmd_name = 'status'
                cmd_args = []
                cmd_kwargs = {}

                state = cmd_func(cmd_name, cmd_args, cmd_kwargs)

                if state is not None:
                    controls['status']['state'] = state

                num_cmds = len(controls['cmd_queue'])

            if state == 'idle' and num_cmds > 0:
                self._run_next_cmd(name)


    def _check_wait(self, name):
        with self._auto_con_lock:
            controls = self._auto_cons[name]

            with controls['cmd_lock']:
                status = controls['status']

                cond = status['condition']

                if cond == 'time':
                    t_wait = status['t_wait']
                    t_start = status['t_start']

                    if time.time() - t_start > t_wait:
                        wait_done = True
                    else:
                        wait_done = False

                elif cond == 'status':
                    inst_conds = status['inst_conds']

                    wait_done = True

                    for con, state_list in inst_conds:
                        cur_state = self._auto_cons[con]['status']['state']

                        if cur_state not in state_list:
                            wait_done = False
                            break

                if wait_done:
                    controls['status'] = {'state': 'idle'}

                num_cmds = len(controls['cmd_queue'])

            if wait_done and num_cmds > 0:
                self._run_next_cmd(name)


    def _run_next_cmd(self, name):
        with self._auto_con_lock:
            controls = self._auto_cons[name]

            with controls['cmd_lock']:
                next_cmd = controls['cmd_queue'].popleft()
                cmd_func = controls['cmd_func']


                cmd_name = next_cmd['cmd']
                cmd_args = next_cmd['args']
                cmd_kwargs = next_cmd['kwargs']

                if cmd_name != 'wait':
                    state = cmd_func(cmd_name, cmd_args, cmd_kwargs)

                    if state is not None:
                        controls['status']['state'] = state

                else:
                    status = cmd_kwargs
                    status['state'] = 'wait'

                    if status['condition'] == 'time':
                        status['t_start'] = time.time()

                    controls['status'] = status

    def add_control(self, name, con_type, cmd_func, current_state='idle'):

        controls = {
            'type'      : con_type, #Defines control type. E.g. hplc_pump1, hplc_pump2, batch
            'cmd_queue' : deque(),
            'cmd_func'  : cmd_func, #Function callback that runs the command
            'cmd_id'    : 0,
            'status'    : {'state': current_state},
            'cmd_lock'  : threading.RLock()
            }

        with self._auto_con_lock:
            self._auto_cons[name] = controls

    def add_cmd(self, name, cmd_name, cmd_args, cmd_kwargs):
        """
        Special commands include:
        wait - Tells instrument to wait for a condtion. Expects additional
            parameters to be paased in via the cmd_kwargs, ignores the cmd_args.
            kwargs must include:
                'conditon', which is the condition to wait for. May ether be
                'time' or 'status', which waits for a fixed amount of time,
                or waits for a specific status or set of status from another
                control in the automator.

            'time' kwargs include:
                *   't_wait' - How long to wait for in s

            'status' kwargs include:
                *   'inst_conds' - A list where each item is a list with the
                    first element being the instrument name (corresponding to
                    control name) to get the status for and the second element
                    being a list of states that will allow the wait to finish.
                    Note that all conditions in the list must be met for the
                    wait to finish.
        """
        with self._auto_con_lock:
            cmd_queue = self._auto_cons[name]['cmd_queue']
            cmd_lock = self._auto_cons[name]['cmd_lock']
            cmd_id = self._auto_cons[name]['cmd_id']

        with cmd_lock:
            cur_id = copy.copy(cmd_id)
            cmd = {
                'cmd_id': cur_id,
                'cmd': cmd_name,
                'args': cmd_args,
                'kwargs': cmd_kwargs
                }
            cmd_queue.append(cmd)

        with self._auto_con_lock:
            self._auto_cons[name]['cmd_id'] += 1

        return cur_id

    def remove_cmd(self, name, cmd_id):
        with self._auto_con_lock:
            cmd_queue = self._auto_cons[name]['cmd_queue']
            cmd_lock = self._auto_cons[name]['cmd_lock']

        found_id, index = self._find_cmd(cmd_queue, cmd_lock, cmd_id)

        if found_id:
            with cmd_lock:
                cmd_queue.remove(cmd_queue[index])

        return found_id

    def reorder_cmd(self, name, cmd_id, rel_position):
        """
        Reorders where the command is in the queue, by a relative position change
        So if rel_position is 1, it moves up one, if it's 2, up 2, -1, down 1, etc.
        """

        with self._auto_con_lock:
            cmd_queue = self._auto_cons[name]['cmd_queue']
            cmd_lock = self._auto_cons[name]['cmd_lock']

        found_id, index = self._find_cmd(cmd_queue, cmd_lock, cmd_id)

        if found_id:
            with cmd_lock:
                item = cmd_queue[index]
                cmd_queue.remove(item)
                cmd_queue.insert(index-rel_position, item)

        return found_id

    def _find_cmd(self, cmd_queue, cmd_lock, cmd_id):
        with cmd_lock:
            found_id = False
            for index, item in enumerate(cmd_queue):
                if item['cmd_id'] == cmd_id:
                    found_id = True
                    break

        return found_id, index

    def get_automator_state(self):
        with self._state_lock:
            state = copy.copy(self._state)

        return state

    def set_automator_state(self, state):
        """
        Sets automator state. Expected states are either 'run' or 'pause',
        which means the automator is actively running commands, or is paused
        and waiting to resume commands. Note that pausing the automator will
        not pause actively running commands on instruments, those must be
        separately paused in the particular instrument control.
        """
        with self._state_lock:
            self._state = state

    def set_control_status(self, name, status_dict):
        """
        Can be used to directly set the control state if needed. Expects
        the status_dict to match what would be set for that status by the
        automator
        """
        with self._auto_con_lock:
            self._auto_conds[name]['status'] = status_dict

    def abort(self):
        self._abort_event.set()

    def _abort(self):

        self._abort_event.clear()
        logger.debug("Automator thread %s aborted", self.name)

    def stop(self):
        """Stops the thread cleanly."""
        logger.info("Starting to clean up and shut down automator thread: %s", self.name)
        self._stop_event.set()


class AutoPanel(wx.Panel):
    """
    This creates the metadata panel.
    """
    def __init__(self, settings, *args, **kwargs):
        """
        Initializes the metadata panel. Accepts the usual wx.Panel arguments plus
        the following.
        """
        wx.Panel.__init__(self, *args, **kwargs)

        self.settings = settings

        self.automator = settings['automator_thread']

        self._create_layout()
        self._init_values()

        self.SetMinSize(self._FromDIP((1000, 400)))

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _init_values(self):
        pass

    def _create_layout(self):

        ctrl_parent = self

        self.status_panel = wx.Panel(ctrl_parent)
        self.auto_list_panel = AutoListPanel(self.settings, ctrl_parent)

        self.top_sizer = wx.BoxSizer(wx.VERTICAL)
        self.top_sizer.Add(self.status_panel, proportion=1,
            border=self._FromDIP(5), flag=wx.EXPAND|wx.ALL)
        self.top_sizer.Add(self.auto_list_panel, proportion=1,
            border=self._FromDIP(5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM)

        self.SetSizer(self.top_sizer)


class AutoListPanel(wx.Panel):
    def __init__(self, settings, *args, **kwargs):
        wx.Panel.__init__(self, *args, **kwargs)

        self.settings = settings

        self.automator = settings['automator_thread']

        self._create_layout()
        self._init_values()

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _init_values(self):
        # Initialize automator controls
        pass

    def _create_layout(self):
        self.top_list_ctrl = self._create_list_layout()
        self.top_settings_ctrl = self._create_settings_layout()

        self.top_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.top_sizer.Add(self.top_list_ctrl, proportion=1,
            flag=wx.RIGHT|wx.EXPAND, border=self._FromDIP(5))
        self.top_sizer.Add(self.top_settings_ctrl, proportion=1,
            flag=wx.EXPAND)

        self.SetSizer(self.top_sizer)

    def _create_list_layout(self):
        self.auto_list = AutoList(self._on_add_item_callback, self)

        return self.auto_list

    def _create_settings_layout(self):
        return wx.Panel(self)

    def _on_add_item_callback(self, item_info):
        item_type == item_info['item_type']

        if item_type == 'sample':
            """
            Check various things, inclucing:
                *   Is there enough buffer to do the run
                *   Do we need to add an instrument switch or an equlibration
                    (should this be checked in the auto list, so that it can add
                    an equilibraiton item or switch item above this?)
            """
            # Something like this. Arguments need refining, needs testing
            self.add_cmd('exp', 'wait', [], {'condition': 'status',
                'inst_conds': [['hplc', 'wait'], ['exp', 'wait']]})
            self.add_cmd('exp', 'expose', [], item_info)
            self.add_cmd('hplc', 'wait', [], {'condition': 'status',
                'inst_conds': [['hplc', 'wait'], ['exp', 'run']]})
            self.add_cmd('hplc', 'inject', [], item_info)


    def _on_remove_item_callback(self, cmd_list):
        self.automator.set_automator_state('pause')

        for cmd in cmd_list:
            self.automator.remove_cmd(cmd[0], cmd[1])

        self.automator.set_automator_state('run')


class AutoList(utils.ItemList):
    def __init__(self, on_add_item_callback, on_remove_item_callback, *args,
        **kwargs):
        utils.ItemList.__init__(self, *args)

        self._on_add_item_callback = on_add_item_callback
        self._on_remove_item_callback = on_remove_item_callback

    def _create_buttons(self):
        button_parent = self

        add_item_btn = wx.Button(button_parent, label='Add Action')
        add_item_btn.Bind(wx.EVT_BUTTON, self._on_add_item)

        remove_item_btn = wx.Button(button_parent, label='Remove Action')
        remove_item_btn.Bind(wx.EVT_BUTTON, self._on_removeitem)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(add_item_btn, border=self._FromDIP(5), flag=wx.LEFT)
        button_sizer.Add(remove_item_btn, border=self._FromDIP(5), flag=wx.LEFT)

        return button_sizer

    def _on_add_item(self, evt):
        # Call a dialog to get item information
        item_info = {
            'item_type' : 'sample',
            'descrip'   : 'Test sample',
            'conc'      : '1',
            'buf'       : 'Test buffer'
            }

        # self._on_add_item_callback(item_info)

        item_type = item_info['item_type']

        new_item = AutoListItem(self, item_type, 'test', 0)

        if item_type == 'sample':
            descrip = item_info['descrip']
            conc = item_info['conc']
            buf = item_info['buf']
            new_item.set_description(descrip)
            new_item.set_concentration(conc)
            new_item.set_buffer(buf)

        self.add_items([new_item])

    def _on_remove_item(self, evt):
        sel_items = self.get_selected_items()

        item_list = [item.automator_name, item.automator_id for item in sel_items]

        # self._on_remove_item_callback(item_list)

        self.remove_selected_items()



class AutoListItem(utils.ListItem):
    def __init__(self, item_list, item_type, auto_name, auto_id, *args, **kwargs):
        self.item_type = item_type

        utils.ListItem.__init__(self, item_list, *args, **kwargs)

        self.automator_name = auto_name
        self.automator_id = auto_id

    def _create_layout(self):
        item_parent = self

        type_label = wx.StaticText(item_parent, label=self.item_type.capitalize(),
            size=self._FromDIP((70, -1)), style=wx.ST_NO_AUTORESIZE)

        fsize = self.GetFont().GetPointSize()
        font = wx.Font(fsize, wx.DEFAULT, wx.NORMAL, wx.BOLD)
        type_label.SetFont(font)

        self.text_list.append(type_label)

        if self.item_type == 'sample':

            desc_label = wx.StaticText(item_parent, label='Descripton:')
            self.desc_ctrl = wx.StaticText(item_parent, label='')

            conc_label = wx.StaticText(item_parent, label='Conc. (mg/ml):')
            self.conc_ctrl = wx.StaticText(item_parent, label='')

            buffer_label = wx.StaticText(item_parent, label='Buffer:')
            self.buffer_ctrl = wx.StaticText(item_parent, label='')

            top_sizer = wx.BoxSizer(wx.HORIZONTAL)
            top_sizer.Add(type_label, flag=wx.RIGHT, border=self._FromDIP(5))
            top_sizer.Add(desc_label, flag=wx.RIGHT, border=self._FromDIP(3))
            top_sizer.Add(self.desc_ctrl, flag=wx.RIGHT, border=self._FromDIP(3))
            top_sizer.Add(conc_label, flag=wx.RIGHT, border=self._FromDIP(3))
            top_sizer.Add(self.conc_ctrl, flag=wx.RIGHT, border=self._FromDIP(3))
            top_sizer.Add(buffer_label, flag=wx.RIGHT, border=self._FromDIP(3))
            top_sizer.Add(self.buffer_ctrl, flag=wx.RIGHT, border=self._FromDIP(3))

            self.text_list.extend([desc_label, self.desc_ctrl,
                conc_label, self.conc_ctrl, buffer_label, self.buffer_ctrl])

        self.SetSizer(top_sizer)


    def set_description(self, descrip):
        self.desc_ctrl.SetLabel(descrip)

    def set_concentration(self, concentration):
        self.conc_ctrl.SetLabel('{}'.format(concentration))

    def set_buffer(self, buffer_info):
        self.buffer_ctrl.SetLabel(buffer_info)



class AutoFrame(wx.Frame):
    """
    A lightweight automator frame that holds the :mod:`ParamPanel`.
    """
    def __init__(self, name, settings, *args, **kwargs):
        """
        Initializes the automator frame. Takes all the usual wx.Frame arguments
        """
        wx.Frame.__init__(self, *args, **kwargs)

        self.name = name

        self._create_layout(settings)

        self.Layout()
        self.Fit()
        self.Layout()

        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _create_layout(self, settings):
        """
        Creates the layout
        """
        self.panel = AutoPanel(settings, parent=self)

        top_sizer = wx.BoxSizer(wx.HORIZONTAL)
        top_sizer.Add(self.panel, 1, wx.EXPAND)

        self.panel.Layout()
        self.panel.Fit()
        self.panel.Layout()

        self.SetSizer(top_sizer)

    def _on_close(self, evt):
        self.Destroy()



def test_cmd_func(name, args, kwargs):
    print(name)
    print(args)
    print(kwargs)
    return 'idle'


if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    # h1.setLevel(logging.DEBUG)
    h1.setLevel(logging.INFO)
    # h1.setLevel(logging.WARNING)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)


    automator = Automator()
    automator.start()

    # automator.add_control('test', 'test1', test_cmd_func)
    # automator.add_control('test2', 'test1', test_cmd_func)
    # automator.add_cmd('test', 'test1cmd', ['testargs'], {'arg1:' 'testkwargs'})
    # automator.add_cmd('test', 'wait', [], {'condition' : 'time', 't_wait': 15})
    # automator.add_cmd('test', 'test1cmd2', ['testargs2'], {'arg1:' 'testkwargs2'})

    # automator.add_cmd('test2', 'wait', [], {'condition' : 'time', 't_wait': 30})
    # automator.add_cmd('test', 'wait', [], {'condition' : 'status', 'inst_conds': [['test2', ['idle']], ]})
    # automator.add_cmd('test2', 'test2cmd', ['testargs'], {'arg1:' 'testkwargs'})
    # automator.add_cmd('test', 'test1cmd3', ['testargs3'], {'arg1:' 'testkwargs3'})

    automator_settings = {
        'automator_thread'  : automator,
        }


    app = wx.App()
    logger.debug('Setting up wx app')
    frame = AutoFrame('AutoFrame', automator_settings, parent=None,
        title='Automator Control')
    frame.Show()
    app.MainLoop()

    if automator is not None:
        automator.stop()
        automator.join()

