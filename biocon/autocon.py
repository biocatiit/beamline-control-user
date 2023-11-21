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
import biohplccon


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

        self._on_run_cmd_callbacks = []
        self._on_finish_cmd_callbacks = []
        self._on_error_cmd_callbacks = []
        self._on_state_change_callbacks = []
        self._on_abort_callbacks = []

        self._cmd_id = 0
        self._wait_id = 0

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

                        if not state.startswith('wait'):
                            self._check_status(name)

                        elif state.startswith('wait'):
                            self._check_wait(name)

                        state = controls['status']['state']

                        if state == 'idle':
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

            state = self._inner_check_status(name)

            with controls['cmd_lock']:
                old_state = copy.copy(controls['status']['state'])

                if state is not None:
                    controls['status']['state'] = state

                num_cmds = len(controls['cmd_queue'])

            if state == 'idle' and old_state != 'idle':
                with controls['cmd_lock']:
                    prev_cmd_id = copy.copy(controls['run_id'])
                queue_name = copy.copy(name)

                state = self.get_automator_state()
                for finish_callback in self._on_finish_cmd_callbacks:
                    finish_callback(prev_cmd_id, queue_name, state)

            # if state == 'idle' and num_cmds > 0:
            #     logger.info('running next cmd from status')
            #     self._run_next_cmd(name)

    def _inner_check_status(self, name):
        with self._auto_con_lock:
            controls = self._auto_cons[name]

            with controls['cmd_lock']:
                cmd_func = controls['cmd_func']

                cmd_name = 'status'
                cmd_args = []
                cmd_kwargs = {'inst_name': name}

                state, success = cmd_func(cmd_name, cmd_args, cmd_kwargs)

                try:
                    state, success = cmd_func(cmd_name, cmd_args, cmd_kwargs)
                except Exception:
                    logger.error('Automator: {} failed to get status')
                    success = False

                    for error_callback in self._on_error_cmd_callbacks:
                        error_callback(-1, 'status', name)

        return state

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

                        if cur_state.startswith('wait_finish'):
                            inst_state = self._inner_check_status(con)

                            if inst_state != 'idle':
                                wait_done = False
                                break

                        elif cur_state.startswith('wait_cmd'):
                            inst_state = self._inner_check_status(con)
                            if inst_state not in state_list:
                                wait_done = False
                                break

                        else:
                            if cur_state not in state_list:
                                wait_done = False
                                break

                if wait_done:
                    self._check_status(name)

                num_cmds = len(controls['cmd_queue'])

            # if wait_done and num_cmds > 0:
            #     logger.info('running next cmd from wait')
            #     self._run_next_cmd(name)


    def _run_next_cmd(self, name):
        with self._auto_con_lock:
            controls = self._auto_cons[name]

            with controls['cmd_lock']:
                next_cmd = controls['cmd_queue'].popleft()
                cmd_func = controls['cmd_func']


                cmd_name = next_cmd['cmd']
                cmd_args = next_cmd['args']
                cmd_kwargs = next_cmd['kwargs']
                cmd_id = next_cmd['cmd_id']

                logger.info(('Automator: {} running cmd {} with args {} and kwargs'
                    ' {}').format(name, cmd_name, cmd_args, cmd_kwargs))

                prev_cmd_id = copy.copy(controls['run_id'])
                controls['run_id'] = cmd_id

                state = self.get_automator_state()
                for run_callback in self._on_run_cmd_callbacks:
                    run_callback(cmd_id, cmd_name, prev_cmd_id, state)

                if not cmd_name.startswith('wait'):

                    try:
                        ex_state, success = cmd_func(cmd_name, cmd_args, cmd_kwargs)
                    except Exception:
                        logger.error(('Automator: {} failed to run cmd {} with '
                            'args {} and kwargs {}').format(name, cmd_name,
                            cmd_args, cmd_kwargs))
                        success = False

                        for error_callback in self._on_error_cmd_callbacks:
                            error_callback(cmd_id, cmd_name, name)

                    if success:
                        state = self._inner_check_status(name)

                        if state != ex_state:
                            wait_id = self.get_wait_id()

                            status = {'state': 'wait_cmd_{}'.format(wait_id),
                                'condition': 'status',
                                'inst_conds': [[name, [ex_state,]]]}

                            controls['status'] = status

                else:
                    status = cmd_kwargs
                    status['state'] = cmd_name

                    if status['condition'] == 'time':
                        status['t_start'] = time.time()

                    controls['status'] = status

    def add_control(self, name, con_type, cmd_func, current_state='idle'):

        controls = {
            'type'      : con_type, #Defines control type. E.g. hplc_pump1, hplc_pump2, batch
            'cmd_queue' : deque(),
            'cmd_func'  : cmd_func, #Function callback that runs the command
            'status'    : {'state': current_state},
            'cmd_lock'  : threading.RLock(),
            'run_id'    : -1,
            }

        with self._auto_con_lock:
            self._auto_cons[name] = controls

    def add_cmd(self, name, cmd_name, cmd_args, cmd_kwargs, at_start=False):
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

        with cmd_lock:
            cur_id = copy.copy(self._cmd_id)
            cmd = {
                'cmd_id': cur_id,
                'cmd': cmd_name,
                'args': cmd_args,
                'kwargs': cmd_kwargs
                }

            if not at_start:
                cmd_queue.append(cmd)
            else:
                cmd_queue.appendleft(cmd)

        with self._auto_con_lock:
            self._cmd_id += 1

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
        index = -1 #In case no items are in the queue

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

    def get_wait_id(self):
        wait_id = copy.copy(self._wait_id)
        self._wait_id += 1

        return wait_id

    def set_automator_state(self, state):
        """
        Sets automator state. Expected states are either 'run' or 'pause',
        which means the automator is actively running commands, or is paused
        and waiting to resume commands. Note that pausing the automator will
        not pause actively running commands on instruments, those must be
        separately paused in the particular instrument control.
        """
        with self._state_lock:
            if self._state != state:
                self._state = state

                for state_callback in self._on_state_change_callbacks:
                    state_callback(state)

    def set_control_status(self, name, status_dict):
        """
        Can be used to directly set the control state if needed. Expects
        the status_dict to match what would be set for that status by the
        automator
        """
        with self._auto_con_lock:
            self._auto_conds[name]['status'] = status_dict

    def add_on_run_cmd_callback(self, callback_func):
        self._on_run_cmd_callbacks.append(callback_func)

    def add_on_finish_cmd_callback(self, callback_func):
        self._on_finish_cmd_callbacks.append(callback_func)

    def add_on_error_cmd_callback(self, callback_func):
        self._on_error_cmd_callbacks.append(callback_func)

    def add_on_state_change_callback(self, callback_func):
        self._on_state_change_callbacks.append(callback_func)

    def add_on_abort_callback(self, callback_func):
        self._on_abort_callbacks.append(callback_func)

    def stop_running_items(self):
        with self._auto_con_lock:
            for name, controls in self._auto_cons.items():
                state = controls['status']['state']

                if (not state.startswith('wait_sample')):
                    old_id = copy.copy(controls['run_id'])
                    self.add_cmd(name, 'abort', [], {'inst_name': name}, at_start=True)
                    self._run_next_cmd(name)

                    if state.startswith('wait_cmd'):
                        controls['status']['state'] = 'idle'

                    elif state.startswith('wait_t'):
                        controls['status']['state'] = 'idle'

                    for abort_callback in self._on_abort_callbacks:
                        abort_callback(old_id,  name)

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

        self.status_panel = AutoStatusPanel(self.settings, ctrl_parent)
        self.auto_list_panel = AutoListPanel(self.settings, ctrl_parent)

        self.top_sizer = wx.BoxSizer(wx.VERTICAL)
        self.top_sizer.Add(self.status_panel, proportion=1,
            border=self._FromDIP(5), flag=wx.EXPAND|wx.ALL)
        self.top_sizer.Add(self.auto_list_panel, proportion=1,
            border=self._FromDIP(5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM)

        self.SetSizer(self.top_sizer)

class AutoStatusPanel(wx.Panel):
    def __init__(self, settings, *args, **kwargs):
        wx.Panel.__init__(self, *args, **kwargs)

        self.settings = settings

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

        self.automator = self.settings['automator_thread']

        self.inst_list = []

        for inst, inst_settings in self.settings['instruments'].items():
            if inst.startswith('hplc'):
                num_paths = inst_settings['num_paths']

                for i in range(num_paths):
                    name = '{}_pump{}'.format(inst, i+1)
                    self.automator.add_control(name, name,
                        inst_settings['automator_callback'])

                    self.inst_list.append(name)

        state = self.automator.get_automator_state()

        if state == 'run':
            self.resume_btn.Disable()
            self.automator_state.SetLabel('Running')
        else:
            self.pause_btn.Disable()
            self.automator_state.SetLabel('Paused')

        self.automator.add_on_state_change_callback(self._on_state_change)

    def _create_layout(self):

        ctrl_parent = self

        self.automator_state = wx.StaticText(ctrl_parent,
            size=self._FromDIP((60,-1)), style=wx.ST_NO_AUTORESIZE)

        status_sizer = wx.BoxSizer(wx.HORIZONTAL)
        status_sizer.Add(wx.StaticText(ctrl_parent, label='Queue status:'),
            flag=wx.ALL, border=self._FromDIP(5))
        status_sizer.Add(self.automator_state, flag=wx.ALL,
            border=self._FromDIP(5))

        self.pause_btn = wx.Button(ctrl_parent, label='Pause queue')
        self.resume_btn = wx.Button(ctrl_parent, label='Resume queue')
        self.stop_btn = wx.Button(ctrl_parent, label='Stop current items')

        self.pause_btn.Bind(wx.EVT_BUTTON, self._on_pause_queue)
        self.resume_btn.Bind(wx.EVT_BUTTON, self._on_resume_queue)
        self.stop_btn.Bind(wx.EVT_BUTTON, self._on_stop_queue)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(self.pause_btn, flag=wx.ALL, border=self._FromDIP(5))
        button_sizer.Add(self.resume_btn, flag=wx.ALL, border=self._FromDIP(5))
        button_sizer.Add(self.stop_btn, flag=wx.ALL, border=self._FromDIP(5))

        self.top_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.top_sizer.Add(status_sizer, proportion=1,
            flag=wx.RIGHT|wx.EXPAND, border=self._FromDIP(5))
        self.top_sizer.Add(button_sizer, proportion=1,
            flag=wx.EXPAND)

        self.SetSizer(self.top_sizer)

    def _on_pause_queue(self, evt):
        self.automator.set_automator_state('pause')
        self.pause_btn.Disable()
        self.resume_btn.Enable()

    def _on_resume_queue(self, evt):
        self.automator.set_automator_state('run')
        self.pause_btn.Enable()
        self.resume_btn.Disable()

    def _on_stop_queue(self, evt):
        self._on_pause_queue(None)
        self.automator.stop_running_items()


    def _on_state_change(self, state):
        if state == 'run':
            wx.CallAfter(self.automator_state.SetLabel, 'Running')
            wx.CallAfter(self.pause_btn.Enable)
            wx.CallAfter(self.resume_btn.Disable)
        else:
            wx.CallAfter(self.automator_state.SetLabel, 'Paused')
            wx.CallAfter(self.pause_btn.Disable)
            wx.CallAfter(self.resume_btn.Enable)



class AutoListPanel(wx.Panel):
    def __init__(self, settings, *args, **kwargs):
        wx.Panel.__init__(self, *args, **kwargs)

        self.settings = settings

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

        self.automator = self.settings['automator_thread']

        self.inst_list = []

        for inst, inst_settings in self.settings['instruments'].items():
            if inst.startswith('hplc'):
                num_paths = inst_settings['num_paths']

                for i in range(num_paths):
                    name = '{}_pump{}'.format(inst, i+1)
                    self.automator.add_control(name, name,
                        inst_settings['automator_callback'])

                    self.inst_list.append(name)

        self.automator.add_on_run_cmd_callback(self._on_automator_run_callback)
        self.automator.add_on_finish_cmd_callback(self._on_automator_finish_callback)
        self.automator.add_on_error_cmd_callback(self._on_automator_error_callback)
        self.automator.add_on_abort_callback(self._on_automator_abort_callback)

        # For testing
        self.automator.add_control('exp', 'exp', test_cmd_func)

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
        self.auto_list = AutoList(self._on_add_item_callback,
            self._on_remove_item_callback, self._on_move_item_callback,
            self, self)

        return self.auto_list

    def _create_settings_layout(self):
        return wx.Panel(self)

    def _on_add_item_callback(self, item_info):
        item_type = item_info['item_type']

        if item_type == 'sec_sample':
            """
            Check various things, inclucing:
                *   Is there enough buffer to do the run
                *   Do we need to add an instrument switch or an equlibration
                    (should this be checked in the auto list, so that it can add
                    an equilibraiton item or switch item above this?)
            """
            # Something like this. Arguments need refining, needs testing
            hplc_inst = item_info['inst']

            sample_wait_id = self.automator.get_wait_id()

            finish_wait_id = self.automator.get_wait_id()
            finish_wait_cmd = 'wait_finish_{}'.format(finish_wait_id)

            sample_wait_cmd = 'wait_sample_{}'.format(sample_wait_id)
            cmd_id1 = self.automator.add_cmd('exp', sample_wait_cmd, [], {'condition': 'status',
                'inst_conds': [[hplc_inst, [sample_wait_cmd,]], ['exp', [sample_wait_cmd,]]]})
            cmd_id2 = self.automator.add_cmd('exp', 'expose', [], item_info)
            cmd_id3 = self.automator.add_cmd('exp', finish_wait_cmd, [], {'condition': 'status',
                'inst_conds': [[hplc_inst, ['idle',]], ['exp', ['idle',]]]})

            inj_settings = {
                'sample_name'   : item_info['sample_name'],
                'acq_method'    : item_info['acq_method'],
                'sample_loc'    : item_info['sample_loc'],
                'inj_vol'       : item_info['inj_vol'],
                'flow_rate'     : item_info['flow_rate'],
                'elution_vol'   : item_info['elution_vol'],
                'flow_accel'    : item_info['flow_accel'],
                'pressure_lim'  : item_info['pressure_lim'],
                'result_path'   : item_info['result_path'],
                'sp_method'     : item_info['sp_method'],
                'wait_for_flow_ramp'    : item_info['wait_for_flow_ramp'],
                'settle_time'   : item_info['settle_time'],
                }

            cmd_id4 = self.automator.add_cmd(hplc_inst, sample_wait_cmd, [],
                {'condition': 'status', 'inst_conds': [[hplc_inst,
                [sample_wait_cmd,]], ['exp', ['idle',]]]})
            cmd_id5 = self.automator.add_cmd(hplc_inst, 'inject', [], inj_settings)
            cmd_id6 = self.automator.add_cmd(hplc_inst, finish_wait_cmd, [],
                {'condition': 'status', 'inst_conds': [[hplc_inst,
                ['idle',]], ['exp', ['idle',]]]})

            #accounts for delayed update time between run queue and instrument status
            cmd_id7 = self.automator.add_cmd(hplc_inst, 'wait_time', [],
                {'condition': 'time', 't_wait': 1})

            auto_names = ['exp', 'exp', 'exp', hplc_inst, hplc_inst, hplc_inst, hplc_inst]
            auto_ids = [cmd_id1, cmd_id2, cmd_id3, cmd_id4, cmd_id5, cmd_id6, cmd_id7]

        elif item_type == 'equilibrate':
            hplc_inst = item_info['inst']

            equil_settings = {
                'equil_rate'    : item_info['equil_rate'],
                'equil_vol'     : item_info['equil_vol'],
                'equil_accel'   : item_info['equil_accel'],
                'purge'         : item_info['purge'],
                'purge_rate'    : item_info['purge_rate'],
                'purge_volume'  : item_info['purge_volume'],
                'purge_accel'   : item_info['purge_accel'],
                'equil_with_sample' : item_info['equil_with_sample'],
                'stop_after_equil'  : item_info['stop_after_equil'],
                'flow_path'     : item_info['flow_path'],
                }

            finish_wait_id = self.automator.get_wait_id()
            finish_wait_cmd = 'wait_finish_{}'.format(finish_wait_id)

            # cmd_id1 = self.automator.add_cmd(hplc_inst, 'wait', [],
            #     {'condition' : 'status', 'inst_conds': [[hplc_inst, 'idle'],]})
            cmd_id2 = self.automator.add_cmd(hplc_inst, 'equilibrate', [], equil_settings)
            cmd_id3 = self.automator.add_cmd(hplc_inst, finish_wait_cmd, [],
                {'condition' : 'status', 'inst_conds': [[hplc_inst, ['idle',]],]})

            num_paths = item_info['num_flow_paths']

            if num_paths == 1:
                finish_wait_id = self.automator.get_wait_id()
                equil_wait_cmd = 'wait_finish_{}'.format(finish_wait_id)
                cmd_id4 = self.automator.add_cmd('exp', equil_wait_cmd, [],
                {'condition' : 'status', 'inst_conds': [[hplc_inst, ['equil',]],]})
                cmd_id5 = self.automator.add_cmd('exp', finish_wait_cmd, [],
                {'condition' : 'status', 'inst_conds': [[hplc_inst, ['idle',]],]})

            # auto_names = [hplc_inst, hplc_inst, hplc_inst]
            # auto_ids = [cmd_id1, cmd_id2, cmd_id3]

            if num_paths == 1:
                auto_names = [hplc_inst, hplc_inst, 'exp', 'exp']
                auto_ids = [cmd_id2, cmd_id3, cmd_id4, cmd_id5]
            else:
                auto_names = [hplc_inst, hplc_inst]
                auto_ids = [cmd_id2, cmd_id3]

        elif item_type == 'switch_pumps':
            auto_names = []
            auto_ids = []

            hplc_inst = item_info['inst']

            switch_settings = {
                'purge_rate'    : item_info['purge_rate'],
                'purge_volume'  : item_info['purge_volume'],
                'purge_accel'   : item_info['purge_accel'],
                'restore_flow_after_switch' : item_info['restore_flow_after_switch'],
                'switch_with_sample'    : item_info['switch_with_sample'],
                'stop_flow1'    : item_info['stop_flow1'],
                'stop_flow2'    : item_info['stop_flow2'],
                'purge_active'  : item_info['purge_active'],
                'flow_path'     : item_info['flow_path'],
                }

            num_paths = item_info['num_flow_paths']

            switch_wait_id = self.automator.get_wait_id()

            switch_wait_cmd = 'wait_switch_{}'.format(switch_wait_id)

            inst_conds = [['{}_pump{}'.format(hplc_inst, i+1), [switch_wait_cmd,]]
                for i in range(num_paths)]

            for i in range(num_paths):
                cmd_name = '{}_pump{}'.format(hplc_inst, i+1)

                cmd_id = self.automator.add_cmd(cmd_name, switch_wait_cmd, [],
                    {'condition': 'status', 'inst_conds': inst_conds})

                auto_names.append(cmd_name)
                auto_ids.append(cmd_id)

            cmd_name = '{}_pump{}'.format(hplc_inst, item_info['flow_path'])

            cmd_id = self.automator.add_cmd(cmd_name, 'switch_pumps', [], switch_settings)

            auto_names.append(cmd_name)
            auto_ids.append(cmd_id)


            finish_wait_id = self.automator.get_wait_id()
            finish_wait_cmd = 'wait_finish_{}'.format(finish_wait_id)

            finish_inst_conds = [['{}_pump{}'.format(hplc_inst, i+1), ['idle',]]
                for i in range(num_paths)]

            for i in range(num_paths):
                cmd_name = '{}_pump{}'.format(hplc_inst, i+1)

                cmd_id = self.automator.add_cmd(cmd_name, finish_wait_cmd, [],
                    {'condition': 'status', 'inst_conds': finish_inst_conds})

                auto_names.append(cmd_name)
                auto_ids.append(cmd_id)

        return auto_names, auto_ids

    def _on_remove_item_callback(self, cmd_list):
        state = self.automator.get_automator_state()

        if state == 'run':
            self.automator.set_automator_state('pause')

        for cmds in cmd_list:
            for i in range(len(cmds[0])):
                cmd_name =  cmds[0][i]
                cmd_id = cmds[1][i]
                self.automator.remove_cmd(cmd_name, cmd_id)

        if state == 'run':
            self.automator.set_automator_state('run')

    def _on_automator_run_callback(self, aid, cmd_name, prev_aid, state):
        wx.CallAfter(self.auto_list.set_item_status, prev_aid, 'done', state)
        if cmd_name.startswith('wait'):
            wx.CallAfter(self.auto_list.set_item_status, aid, 'wait', state)
        else:
            wx.CallAfter(self.auto_list.set_item_status, aid, 'run', state)

    def _on_automator_finish_callback(self, prev_aid, queue_name, state):
        wx.CallAfter(self.auto_list.set_item_status, prev_aid, 'done', state)

    def _on_move_item_callback(self, aid, cmd_name, dist):
        self.automator.reorder_cmd(cmd_name, aid, dist)

    def _on_automator_error_callback(self, aid, cmd_name, inst_name):
        pass # Do something with the errors here

    def _on_automator_abort_callback(self, aid, name):
        print('in abort callback')
        wx.CallAfter(self.auto_list.abort_item, aid)

class AutoList(utils.ItemList):
    def __init__(self, on_add_item_callback, on_remove_item_callback,
        on_move_item_callback, auto_panel, *args, **kwargs):
        utils.ItemList.__init__(self, *args)

        self.auto_panel = auto_panel

        self._on_add_item_callback = on_add_item_callback
        self._on_remove_item_callback = on_remove_item_callback
        self._on_move_item_callback = on_move_item_callback

    def _create_buttons(self):
        button_parent = self

        add_item_btn = wx.Button(button_parent, label='Add Action')
        add_item_btn.Bind(wx.EVT_BUTTON, self._on_add_item)

        remove_item_btn = wx.Button(button_parent, label='Remove Action')
        remove_item_btn.Bind(wx.EVT_BUTTON, self._on_remove_item)

        move_item_up_btn = wx.Button(button_parent, label='Move up')
        move_item_up_btn.Bind(wx.EVT_BUTTON, self._on_move_item_up)

        move_item_down_btn = wx.Button(button_parent, label='Move down')
        move_item_down_btn.Bind(wx.EVT_BUTTON, self._on_move_item_down)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(add_item_btn, border=self._FromDIP(5), flag=wx.LEFT)
        button_sizer.Add(remove_item_btn, border=self._FromDIP(5), flag=wx.LEFT)
        button_sizer.Add(move_item_up_btn, border=self._FromDIP(5),
            flag=wx.LEFT)
        button_sizer.Add(move_item_down_btn, border=self._FromDIP(5),
            flag=wx.LEFT)

        return button_sizer

    def _on_add_item(self, evt):
        # Call a dialog to get item information
        # Stand in:
        dialog = wx.SingleChoiceDialog(self, 'Pick an action to add',
            'Pick an action to add to the queue',
            ['Run SEC-SAXS sample', 'Equilibrate column',
            'Switch pumps'])

        res = dialog.ShowModal()

        if res == wx.ID_OK:
            choice = dialog.GetStringSelection()

        else:
            choice = None

        dialog.Destroy()

        if choice is not None:
            # Stands in for getting info from a dialog or other location

            if choice == 'Run SEC-SAXS sample':

                item_info = {
                    # General parameters
                    'item_type'     : 'sec_sample',
                    'descrip'       : 'Test sample',
                    'conc'          : '1',
                    'buf'           : 'Test buffer',
                    'inst'          : 'hplc1_pump1',
                    'sample_name'   : 'test',

                    # Injection parameters
                    'acq_method'    : 'SEC-MALS',
                    'sample_loc'    : 'D2F-A1',
                    'inj_vol'       : '10.0',
                    'flow_rate'     : '0.1',
                    'elution_vol'   : '0.1',
                    'flow_accel'    : '0.1',
                    'pressure_lim'  : '60',
                    'result_path'   : '',
                    'sp_method'     : None,
                    'wait_for_flow_ramp'    : True,
                    'settle_time'   : '0.0',
                    'flow_path'     : 1,

                    # Exposure parameters
                    'data_dir'      : '',
                    'exp_time'      : 0.5,
                    'exp_period'    : 1,
                    'exp_num'       : 2,
                    }

                num_flow_paths = self.auto_panel.settings['instruments'][item_info['inst'].split('_')[0]]['num_paths']

                item_info['num_flow_paths'] = num_flow_paths

            elif choice == 'Equilibrate column':
                item_info = {
                    # General aprameters
                    'item_type' : 'equilibrate',
                    'buf'       : 'Test buffer',
                    'inst'      : 'hplc1_pump1',

                    # Equilibrate parameters
                    'equil_rate'    : 0.1,
                    'equil_vol'     : 0.1,
                    'equil_accel'   : 0.1,
                    'purge'         : True,
                    'purge_rate'    : 0.2,
                    'purge_volume'  : 0.2,
                    'purge_accel'   : 0.2,
                    'equil_with_sample' : False,
                    'stop_after_equil'  : False,
                    'flow_path'     : 1,
                }

                num_flow_paths = self.auto_panel.settings['instruments'][item_info['inst'].split('_')[0]]['num_paths']

                item_info['num_flow_paths'] = num_flow_paths

            elif choice == 'Switch pumps':

                item_info = {
                    'item_type' : 'switch_pumps',
                    'inst'      : 'hplc1',

                    #Switch parameters
                    'purge_rate'    : 0.1,
                    'purge_volume'  : 0.1,
                    'purge_accel'   : 0.1,
                    'restore_flow_after_switch' : True,
                    'switch_with_sample'    : False,
                    'stop_flow1'    : True,
                    'stop_flow2'    : True,
                    'purge_active'  : True,
                    'flow_path'     : 1,
                    }

                num_flow_paths = self.auto_panel.settings['instruments'][item_info['inst'].split('_')[0]]['num_paths']

                item_info['num_flow_paths'] = num_flow_paths

            self._add_item(item_info)

    def _add_item(self, item_info):

        auto_names, auto_ids = self._on_add_item_callback(item_info)
        # auto_names = ['test']
        # auto_ids = [0]

        item_type = item_info['item_type']

        new_item = AutoListItem(self, item_type, auto_names, auto_ids, item_info)

        if item_type == 'sec_sample':
            name = item_info['sample_name']
            descrip = item_info['descrip']
            conc = item_info['conc']
            buf = item_info['buf']
            new_item.set_name(name)
            new_item.set_description(descrip)
            new_item.set_concentration(conc)
            new_item.set_buffer(buf)

        elif item_type == 'equilibrate':
            buf = item_info['buf']
            new_item.set_buffer(buf)

        new_item.set_status_label('Queued')

        self.add_items([new_item])


    def _on_remove_item(self, evt):
        sel_items = self.get_selected_items()

        item_list = [[item.automator_names, item.automator_ids] for item in sel_items]

        self._on_remove_item_callback(item_list)

        self.remove_selected_items()

    def _on_move_item_up(self, evt):
        sel_items = self.get_selected_items()

        if len(sel_items) > 0:
            top_item = sel_items[0]
            top_idx = self.get_item_index(top_item)

            move_up = True

            if top_idx == 0:
                move_up = False

            else:
                prev_states_done = [item.status == 'done' for item in self.all_items[:top_idx]]
                prev_states_abort = [item.status == 'abort' for item in self.all_items[:top_idx]]

                if all(prev_states_done) or all(prev_states_abort):
                    move_up = False

            if move_up:
                self.auto_panel.automator.set_automator_state('pause')

                do_move = self._check_move_status(sel_items, 'up')

                if do_move:
                    for item in sel_items:
                        self._do_move_item(item, 'up')

                self.auto_panel.automator.set_automator_state('run')

    def _on_move_item_down(self, evt):
        sel_items = self.get_selected_items()

        if len(sel_items) > 0:

            bot_item = sel_items[-1]
            bot_idx = self.get_item_index(bot_item)

            move_down = True

            if bot_idx == len(self.all_items)-1:
                move_down = False

            else:
                item_states_done = [item.status == 'done' for item in sel_items]
                item_states_abort = [item.status == 'abort' for item in self.all_items[:top_idx]]

                if any(item_states_done) or all(prev_states_abort):
                    move_down = False

            if move_down:
                self.auto_panel.automator.set_automator_state('pause')

                do_move = self._check_move_status(sel_items, 'down')

                if do_move:
                    for item in sel_items:
                        self._do_move_item(item, 'down')

                self.auto_panel.automator.set_automator_state('run')

    def _check_move_status(self, sel_items, move):
        do_move = True

        for item in sel_items:
            item_idx = self.get_item_index(item)

            if move == 'up':
                switch_item = self.all_items[item_idx-1]
            else:
                switch_item = self.all_items[item_idx+1]

            share_control = False

            for name in item.automator_names:
                if name in switch_item.automator_names:
                    share_control = True
                    break

            if (any([status != 'queue' for status in switch_item.automator_id_status])
                and share_control):
                do_move = False
                break

        return do_move

    def _do_move_item(self, item, move):
        item_idx = self.get_item_index(item)

        if move == 'up':
            switch_item = self.all_items[item_idx-1]
            move_list = enumerate(item.automator_names)
        else:
            switch_item = self.all_items[item_idx+1]
            move_list = enumerate(item.automator_names)
            move_list = list(move_list)[::-1]

        for i, name in move_list:
            move_dist = self._get_shared_cmd_number(name, switch_item)

            if move_dist > 0:
                if move == 'down':
                    move_dist *= -1

                self._on_move_item_callback(item.automator_ids[i],
                    name, move_dist)

        self.move_item(item, move, False)

        self.resize_list()

    def _get_shared_cmd_number(self, cmd_name, item):
        return item.automator_names.count(cmd_name)

    def set_item_status(self, aid, status, state):
        for item in self.all_items:
            if item.status != 'done' and item.status != 'abort':
                if aid in item.automator_ids:
                    item.set_automator_status(aid, status, state)
                    break

    def abort_item(self, aid):
        print('in abort item')
        for item in self.all_items:
            print(item.status)
            if item.status != 'done' and item.status != 'abort':
                print('aborting')
                print(aid)
                print(item.automator_ids)
                if aid in item.automator_ids:
                    print('aborting2')
                    item.abort(aid)


class AutoListItem(utils.ListItem):
    def __init__(self, item_list, item_type, auto_names, auto_ids, item_info,
        *args, **kwargs):
        self.item_type = item_type
        self.item_info = item_info

        utils.ListItem.__init__(self, item_list, *args, **kwargs)

        self.automator_names = auto_names
        self.automator_ids = auto_ids
        self.automator_id_status = ['queue' for aid in self.automator_ids]
        self.status = 'queue'

    def _create_layout(self):
        item_parent = self

        if self.item_type == 'sec_sample':
            item_label = 'SEC sample'
        else:
            item_label = self.item_type.capitalize()

        if self.item_info['inst'].startswith('hplc'):
            if self.item_info['num_flow_paths'] == 1:
                inst_label = self.item_info['inst'].split('_')[0].upper()
            else:
                inst_label = '{} {}'.format(self.item_info['inst'].split('_')[0].upper(),
                    self.item_info['inst'].split('_')[1].capitalize())

        item_label = '{}, {}'.format(item_label, inst_label)

        type_label = wx.StaticText(item_parent, label=item_label,
            size=self._FromDIP((175, -1)), style=wx.ST_NO_AUTORESIZE)

        fsize = self.GetFont().GetPointSize()
        font = wx.Font(fsize, wx.DEFAULT, wx.NORMAL, wx.BOLD)
        type_label.SetFont(font)

        self.text_list.append(type_label)

        self.status_ctrl = wx.StaticText(item_parent, label='',
            size=self._FromDIP((50, -1)), style=wx.ST_NO_AUTORESIZE)

        std_sizer = wx.BoxSizer(wx.HORIZONTAL)
        std_sizer.Add(type_label, flag=wx.RIGHT|wx.LEFT, border=self._FromDIP(5))
        std_sizer.Add(wx.StaticText(item_parent, label='Status:'), flag=wx.RIGHT,
            border=self._FromDIP(5))
        std_sizer.Add(self.status_ctrl, flag=wx.RIGHT|wx.LEFT,
            border=self._FromDIP(5))

        if self.item_type == 'sec_sample':

            name_label = wx.StaticText(item_parent, label='Name:')
            self.name_ctrl = wx.StaticText(item_parent, label='')

            desc_label = wx.StaticText(item_parent, label='Descripton:')
            self.desc_ctrl = wx.StaticText(item_parent, label='')

            conc_label = wx.StaticText(item_parent, label='Conc. (mg/ml):')
            self.conc_ctrl = wx.StaticText(item_parent, label='')

            buffer_label = wx.StaticText(item_parent, label='Buffer:')
            self.buffer_ctrl = wx.StaticText(item_parent, label='')

            item_sizer = wx.BoxSizer(wx.HORIZONTAL)
            item_sizer.Add(name_label, flag=wx.RIGHT|wx.LEFT, border=self._FromDIP(5))
            item_sizer.Add(self.name_ctrl, flag=wx.RIGHT, border=self._FromDIP(5))
            item_sizer.Add(desc_label, flag=wx.RIGHT, border=self._FromDIP(5))
            item_sizer.Add(self.desc_ctrl, flag=wx.RIGHT, border=self._FromDIP(5))
            item_sizer.Add(conc_label, flag=wx.RIGHT, border=self._FromDIP(5))
            item_sizer.Add(self.conc_ctrl, flag=wx.RIGHT, border=self._FromDIP(5))
            item_sizer.Add(buffer_label, flag=wx.RIGHT, border=self._FromDIP(5))
            item_sizer.Add(self.buffer_ctrl, flag=wx.RIGHT, border=self._FromDIP(5))

            self.text_list.extend([desc_label, self.desc_ctrl,
                conc_label, self.conc_ctrl, buffer_label, self.buffer_ctrl])

        if self.item_type == 'equilibrate':

            buffer_label = wx.StaticText(item_parent, label='Buffer:')
            self.buffer_ctrl = wx.StaticText(item_parent, label='')

            item_sizer = wx.BoxSizer(wx.HORIZONTAL)
            item_sizer.Add(buffer_label, flag=wx.RIGHT|wx.LEFT,
                border=self._FromDIP(3))
            item_sizer.Add(self.buffer_ctrl, flag=wx.RIGHT, border=self._FromDIP(5))

            self.text_list.extend([buffer_label, self.buffer_ctrl])

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(std_sizer, flag=wx.TOP|wx.BOTTOM, border=self._FromDIP(5))
        top_sizer.Add(item_sizer, flag=wx.BOTTOM, border=self._FromDIP(5))

        self.SetSizer(top_sizer)

    def set_description(self, descrip):
        self.desc_ctrl.SetLabel(descrip)

    def set_concentration(self, concentration):
        self.conc_ctrl.SetLabel('{}'.format(concentration))

    def set_buffer(self, buffer_info):
        self.buffer_ctrl.SetLabel(buffer_info)

    def set_name(self, name):
        self.name_ctrl.SetLabel(name)

    def set_status_label(self, status):
        self.status_ctrl.SetLabel(status)

    def set_automator_status(self, aid, status, state):
        if aid in self.automator_ids:
            index = self.automator_ids.index(aid)

            self.automator_id_status[index] = status

        if all([val == 'queue' for val in self.automator_id_status]):
            self.status = 'queue'
        elif all([val == 'done' for val in self.automator_id_status]):
            self.status = 'done'
        elif (any([val == 'wait' for val in self.automator_id_status]) and
            not any([val == 'run' for val in self.automator_id_status])):
            self.status = 'wait'
        elif any([val == 'run' for val in self.automator_id_status]):
            self.status = 'run'
        elif (state != 'run' and
            not all([val == 'done' for val in self.automator_id_status])):
            self.status = 'pause'

        label = self.status_ctrl.GetLabel()

        if self.status == 'queue' and label != 'Queued':
            self.status_ctrl.SetLabel('Queued')
        elif self.status == 'wait' and label != 'Waiting':
            self.status_ctrl.SetLabel('Waiting')
        elif self.status == 'run' and label != 'Running':
            self.status_ctrl.SetLabel('Running')
        elif self.status == 'done' and label != 'Done':
            self.status_ctrl.SetLabel('Done')
        elif self.status == 'pause' and label != 'Paused':
            self.status_ctrl.SetLabel('Paused')

    def abort(self, aid):
        print('aborting item')
        id_list = [[self.automator_names, self.automator_ids],]

        self.item_list._on_remove_item_callback(id_list)

        self.status = 'abort'
        self.status_ctrl.SetLabel('Aborted')
        print('here')


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
    # if name != 'status':
    #     print(name)
    #     print(args)
    #     print(kwargs)
    pass
    return 'idle', True


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

     # SEC-MALS HPLC-1
    hplc_args = {
        'name'  : 'HPLC-1',
        'args'  : ['AgilentHPLC', 'net.pipe://localhost/Agilent/OpenLAB/'],
        'kwargs': {'instrument_name': 'HPLC-1', 'project_name': 'Demo',
                    'get_inst_method_on_start': True}
        }

    purge1_valve_args = {
        'name'  : 'Purge 1',
        'args'  :['Rheodyne', 'COM5'],
        'kwargs': {'positions' : 6}
        }

    buffer1_valve_args = {
        'name'  : 'Buffer 1',
        'args'  : ['Cheminert', 'COM3'],
        'kwargs': {'positions' : 10}
        }

    # Standard stack for SEC-MALS
    setup_devices = [
        {'name': 'HPLC-1', 'args': ['AgilentHPLCStandard', None],
            'kwargs': {'hplc_args' : hplc_args,
            'purge1_valve_args' : purge1_valve_args,
            'buffer1_valve_args' : buffer1_valve_args,
            'pump1_id' : 'quat. pump#1c#1',
            },
        }
        ]

    # Local
    com_thread = biohplccon.HPLCCommThread('HPLCComm')
    com_thread.start()

    # # Remote
    # com_thread = None

    hplc_settings = {
        # Connection settings for hplc
        'remote'        : False,
        'remote_device' : 'hplc',
        'device_init'   : setup_devices,
        'remote_ip'     : '192.168.1.16',
        'remote_port'   : '5558',
        'com_thread'    : com_thread,
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
        'stop_after_equil'          : True,
        'switch_purge_active'       : True,
        'switch_purge_volume'       : 1,
        'switch_purge_rate'         : 1,
        'switch_purge_accel'        : 10,
        'switch_with_sample'        : False,
        'switch_stop_flow1'         : True,
        'switch_stop_flow2'         : True,
        'restore_flow_after_switch' : True,
        # 'acq_method'                : 'SECSAXS_test',
        'acq_method'                : 'SEC-MALS',
        'sample_loc'                : 'D2F-A1',
        'inj_vol'                   : 10.0,
        'flow_rate'                 : 0.6,
        'flow_accel'                : 0.1,
        'elution_vol'               : 30,
        'sample_pressure_lim'       : 60.0,
        'result_path'               : '',
        'sp_method'                 : '',
        'wait_for_flow_ramp'        : True,
        'settle_time'               : 0.0,
        }

    app = wx.App()
    logger.debug('Setting up wx app')
    hplc_frame = biohplccon.HPLCFrame('HPLCFrame', hplc_settings, parent=None,
        title='HPLC Control')
    hplc_frame.Show()


    hplc_automator_callback = hplc_frame.devices[0].automator_callback
    # hplc_automator_callback = test_cmd_func

    automator_settings = {
        'automator_thread'  : automator,
        'instruments'       : {'hplc1' : {'num_paths': 1,
                                'automator_callback': hplc_automator_callback}}
        }


    """
    Next up to do:

    - Make it so that you can move items up and down in the list

    - There's something weird with the queue when using both equilibration and sample items. Need to figure that out.
        Maybe the status isn't updating correctly? because it's running equil
        as soon as the sample injects, which results in an error. Also not updating
        status on sample after equil correctly (never goes from run to done)

    - Work on making the items look better

    - Add ties to exposure, coflow, add coflow change buffer to the system for
    appropriate commands (equilibrate for single pump, switch pumps for dual pump)

    - Add buffer volume tracking to coflow
    """


    # app = wx.App()
    # logger.debug('Setting up wx app')
    frame = AutoFrame('AutoFrame', automator_settings, parent=None,
        title='Automator Control')
    frame.Show()

    app.MainLoop()

    if automator is not None:
        automator.stop()
        automator.join()

    if com_thread is not None:
        com_thread.stop()
        com_thread.join()

