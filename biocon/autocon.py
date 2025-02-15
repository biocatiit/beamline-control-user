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
import traceback

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import wx
import wx.lib.scrolledpanel as scrolled

import utils
import biohplccon
import coflowcon


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

                    if wait_done:
                        self._check_status(name)

                elif cond == 'status':
                    inst_conds = status['inst_conds']

                    wait_done = True

                    if status['state'].startswith('wait_sync'):
                        all_states = []

                        for con, state_list in inst_conds:
                            cur_state = self._auto_cons[con]['status']['state']

                            if cur_state not in state_list:
                                all_states.append(False)
                            else:
                                all_states.append(True)

                        if not all(all_states):
                            wait_done = False

                        if wait_done:
                            for con, state_list in inst_conds:
                                self._check_status(con)
                            self._check_status(name)

                    else:
                        for con, state_list in inst_conds:
                            cur_state = self._auto_cons[con]['status']['state']
                            if cur_state.startswith('wait_cmd'):
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
                        traceback.print_exc()

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
            parameters to be passed in via the cmd_kwargs, ignores the cmd_args.
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

    def remove_on_run_cmd_callback(self, callback_func):
        if callback_func in self._on_run_cmd_callbacks:
            self._on_run_cmd_callbacks.remove(callback_func)

    def remove_on_finish_cmd_callback(self, callback_func):
        if callback_func in self._on_finish_cmd_callbacks:
            self._on_finish_cmd_callbacks.remove(callback_func)

    def remove_on_error_cmd_callback(self, callback_func):
        if callback_func in self._on_error_cmd_callbacks:
            self._on_error_cmd_callbacks.remove(callback_func)

    def remove_on_state_change_callback(self, callback_func):
        if callback_func in self._on_state_change_callbacks:
            self._on_state_change_callbacks.remove(callback_func)

    def remove_on_abort_callback(self, callback_func):
        if callback_func in self._on_abort_callbacks:
            self._on_abort_callbacks.remove(callback_func)

    def stop_running_items(self):
        with self._auto_con_lock:
            for name, controls in self._auto_cons.items():
                state = controls['status']['state']
                self._inner_stop_item(name, controls, state)

    def _inner_stop_item(self, name, controls, state):
        old_id = copy.copy(controls['run_id'])

        if state.startswith('wait_t') or state.startswith('wait_sync'):
            controls['status']['state'] = 'idle'

        else:
            self.add_cmd(name, 'abort', [], {'inst_name': name}, at_start=True)
            self._run_next_cmd(name)

        for abort_callback in self._on_abort_callbacks:
            abort_callback(old_id,  name)

    def stop_running_item(self, name):
        with self._auto_con_lock:
            controls = self._auto_cons[name]
            state = controls['status']['state']
            self._inner_stop_item(name, controls, state)

    def abort(self):
        self._abort_event.set()

    def _abort(self):

        self._abort_event.clear()
        logger.debug("Automator thread %s aborted", self.name)

    def stop(self):
        """Stops the thread cleanly."""
        logger.info("Starting to clean up and shut down automator thread: %s", self.name)
        self._stop_event.set()

class AutoCommand(object):
    """
    This creates an automator command object which holds all the information,
    such as parameters, command ids, etc, about the individual automator queue
    items that make up a top level command such as change buffer.
    """
    def __init__(self, automator, cmd_info):
        self.automator = automator
        self.cmd_info = cmd_info
        self.auto_names = []
        self.auto_ids = []
        self.status = ''
        self._initialize_cmd(cmd_info)
        self._status_change_callbacks = []

    def _initialize_cmd(self, cmd_info):
        """
        Overwrite in sub classes
        Should define lists of self.auto_names and self.auto_ids that have the
        automator instrument names and command ids for each command submitted,
        in order.
        """
        pass
        self._post_initialize_cmd()

    def _post_initialize_cmd(self):
        """
        Every _initialize command should call this once it runs
        """
        self.auto_id_status = ['queue' for aid in self.auto_ids]
        self.automator.add_on_run_cmd_callback(self._on_automator_run_callback)
        self.automator.add_on_finish_cmd_callback(self._on_automator_finish_callback)

    def _on_automator_run_callback(self, aid, cmd_name, prev_aid, state):
        self.set_command_status(prev_aid, 'done', state)

        if cmd_name.startswith('wait'):
            status = 'wait'
        else:
            status = 'run'

        self.set_command_status(aid, status, state)

    def _on_automator_finish_callback(self, aid, queue_name, state):
        self.set_command_status(aid, 'done', state)

    def set_command_status(self, aid, status, state):
        if aid in self.auto_ids:
            index = self.auto_ids.index(aid)

            self.auto_id_status[index] = status

            old_status = copy.copy(self.status)

            if all([val == 'queue' for val in self.auto_id_status]):
                self.status = 'queue'
            elif all([val == 'done' for val in self.auto_id_status]):
                self.status = 'done'
            elif (any([val == 'wait' for val in self.auto_id_status]) and
                not any([val == 'run' for val in self.auto_id_status])):
                self.status = 'wait'
            elif any([val == 'run' for val in self.auto_id_status]):
                self.status = 'run'
            elif (state != 'run' and
                not all([val == 'done' for val in self.auto_id_status])):
                self.status = 'pause'

            if self.status == 'done':
                self.automator.remove_on_run_cmd_callback(self._on_automator_run_callback)
                self.automator.remove_on_finish_cmd_callback(self._on_automator_finish_callback)

            if old_status != self.status:
                for cb_func in self._status_change_callbacks:
                    cb_func()

    def get_command_status(self):
        """
        Gets the overall command status
        """
        return self.status

    def abort(self):
        """
        Aborts the command
        """
        id_list = [[self.auto_names, self.auto_ids],]

        state = self.automator.get_automator_state()

        if state == 'run':
            self.automator.set_automator_state('pause')

        self.remove_command_from_automator()

        if state == 'run':
            self.automator.set_automator_state('run')

        self.status = 'abort'

    def delete_command(self):
        state = self.automator.get_automator_state()

        if state == 'run':
            self.automator.set_automator_state('pause')

        self.automator.remove_on_run_cmd_callback(self._on_automator_run_callback)
        self.automator.remove_on_finish_cmd_callback(self._on_automator_finish_callback)
        self.remove_command_from_automator()

        if state == 'run':
            self.automator.set_automator_state('run')

    def remove_command_from_automator(self):
        for i in range(len(self.auto_names)):
            cmd_name =  self.auto_names[i]
            cmd_id = self.auto_ids[i]
            cmd_status = self.auto_id_status[i]

            if cmd_status == 'queue':
                self.automator.remove_cmd(cmd_name, cmd_id)
            elif cmd_status == 'run' or cmd_status == 'wait':
                self.automator.stop_running_item(cmd_name)


    def add_status_change_callback(self, callback_func):
        self._status_change_callbacks.append(callback_func)

    def remove_status_change_callback(self, callback_func):
        if callback_func in self._status_change_callbacks:
            self._status_change_callbacks.remove(callback_func)

    def _add_automator_cmd(self, inst, cmd, cmd_args, cmd_kwargs):
        cmd_id = self.automator.add_cmd(inst, cmd, cmd_args, cmd_kwargs)
        self.auto_names.append(inst)
        self.auto_ids.append(cmd_id)

class SecSampleCommand(AutoCommand):
    """
    A command for running a SEC sample and collecting SAXS data
    """
    def __init__(self, *args, **kwargs):
        AutoCommand.__init__(self, *args, **kwargs)

    def _initialize_cmd(self, cmd_info):
        """
        Notes:
        Should synchronize start and finish of exp, hplc, and coflow
        """
        # Something like this. Arguments need refining, needs testing
        hplc_inst = cmd_info['inst']

        sample_wait_id = self.automator.get_wait_id()
        sample_wait_cmd = 'wait_sync_{}'.format(sample_wait_id)
        sample_conds = [[hplc_inst, [sample_wait_cmd,]], ['exp', [sample_wait_cmd,]],
            ['coflow', [sample_wait_cmd,]],]

        finish_wait_id = self.automator.get_wait_id()
        finish_wait_cmd = 'wait_sync_{}'.format(finish_wait_id)
        finish_conds = [[hplc_inst, [finish_wait_cmd,]], ['exp', [finish_wait_cmd,]],
            ['coflow', [finish_wait_cmd,]],]

        finish_wait_id2 = self.automator.get_wait_id()
        finish_wait_cmd2 = 'wait_sync_{}'.format(finish_wait_id)
        finish_conds2 = [[hplc_inst, [finish_wait_cmd2,]], ['exp', [finish_wait_cmd2,]],
            ['coflow', [finish_wait_cmd2,]],]

        self._add_automator_cmd('exp', sample_wait_cmd, [], {'condition': 'status',
            'inst_conds': sample_conds})
        self._add_automator_cmd('exp', 'expose', [], cmd_info)
        self._add_automator_cmd('exp', finish_wait_cmd, [], {'condition': 'status',
            'inst_conds': finish_conds})

        inj_settings = {
            'sample_name'   : cmd_info['filename'],
            'acq_method'    : cmd_info['acq_method'],
            'sample_loc'    : cmd_info['sample_loc'],
            'inj_vol'       : cmd_info['inj_vol'],
            'flow_rate'     : cmd_info['flow_rate'],
            'elution_vol'   : cmd_info['elution_vol'],
            'flow_accel'    : cmd_info['flow_accel'],
            'pressure_lim'  : cmd_info['pressure_lim'],
            'result_path'   : cmd_info['result_path'],
            'sp_method'     : cmd_info['sp_method'],
            'wait_for_flow_ramp'    : cmd_info['wait_for_flow_ramp'],
            'settle_time'   : cmd_info['settle_time'],
            }

        hplc_wait_id = self.automator.get_wait_id()
        hplc_wait_cmd = 'wait_sync_{}'.format(hplc_wait_id)

        self._add_automator_cmd(hplc_inst, sample_wait_cmd, [],
            {'condition': 'status', 'inst_conds': sample_conds})
        self._add_automator_cmd(hplc_inst, hplc_wait_cmd, [],
            {'condition': 'status', 'inst_conds': [[hplc_inst,
            [hplc_wait_cmd,]], ['exp', ['exposing',]]]})
        self._add_automator_cmd(hplc_inst, 'inject', [], inj_settings)
        if cmd_info['stop_flow']:
            self._add_automator_cmd(hplc_inst, 'stop_flow', [],
                {'flow_path': cmd_info['flow_path']})
        #accounts for delayed update time between run queue and instrument status
        self._add_automator_cmd(hplc_inst, 'wait_time', [],
            {'condition': 'time', 't_wait': 1})
        self._add_automator_cmd(hplc_inst, finish_wait_cmd, [],
            {'condition': 'status', 'inst_conds': finish_conds})

        self._add_automator_cmd('coflow', sample_wait_cmd, [],
            {'condition': 'status', 'inst_conds': sample_conds})
        if cmd_info['start_coflow']:
            self._add_automator_cmd('coflow', 'start', [],
                {'flow_rate': cmd_info['coflow_fr']})
        else:
            self._add_automator_cmd('coflow', 'change_flow', [],
                {'flow_rate': cmd_info['coflow_fr']})
        self._add_automator_cmd('coflow', finish_wait_cmd, [],
            {'condition': 'status', 'inst_conds': finish_conds})

        if cmd_info['stop_coflow']:
            self._add_automator_cmd('coflow', 'stop', [], {})
            self._add_automator_cmd('coflow', finish_wait_cmd2, [],
                {'condition': 'status', 'inst_conds': finish_conds2})
            self._add_automator_cmd(hplc_inst, finish_wait_cmd2, [],
                {'condition': 'status', 'inst_conds': finish_conds2})
            self._add_automator_cmd('exp', finish_wait_cmd2, [],
                {'condition': 'status', 'inst_conds': finish_conds2})

        self._post_initialize_cmd()

class EquilibrateCommand(AutoCommand):
    """
    A command for running an equilibration on an HPLC
    """
    def __init__(self, *args, **kwargs):
        AutoCommand.__init__(self, *args, **kwargs)

    def _initialize_cmd(self, cmd_info):
        """
        If this is a single flow path:
        1) Ensure synchronization of hplc, coflow, exp at start
        2) Ensure synchronization of hplc, coflow, exp at end

        If this is a dual flow path: No synchronization of coflow or exp
        In fact, probably don't want to do coflow equil at all for a
        dual flow path, since other path might be running samples.
        """
        # Not finisehd adding in coflow
        hplc_inst = cmd_info['inst']

        equil_settings = {
            'equil_rate'    : cmd_info['equil_rate'],
            'equil_vol'     : cmd_info['equil_vol'],
            'equil_accel'   : cmd_info['equil_accel'],
            'purge'         : cmd_info['purge'],
            'purge_rate'    : cmd_info['purge_rate'],
            'purge_volume'  : cmd_info['purge_volume'],
            'purge_accel'   : cmd_info['purge_accel'],
            'equil_with_sample' : cmd_info['equil_with_sample'],
            'stop_after_equil'  : cmd_info['stop_after_equil'],
            'flow_path'     : cmd_info['flow_path'],
            }

        equil_coflow = cmd_info['coflow_equil']
        num_paths = cmd_info['num_flow_paths']

        start_wait_id = self.automator.get_wait_id()
        start_wait_cmd = 'wait_sync_{}'.format(start_wait_id)
        start_conds = [[hplc_inst, [start_wait_cmd,]],]

        wait_id = self.automator.get_wait_id()
        finish_wait_cmd = 'wait_sync_{}'.format(wait_id)
        finish_conds = [[hplc_inst, [finish_wait_cmd,]],]

        if equil_coflow:
            start_conds.append(['coflow', [start_wait_cmd,]])
            finish_conds.append(['coflow', [finish_wait_cmd,]])

        if num_paths == 1:
            start_conds.append(['exp', [start_wait_cmd,]])
            finish_conds.append(['exp', [finish_wait_cmd,]])

            wait_id = self.automator.get_wait_id()
            start_wait_cmd = 'wait_sync_{}'.format(wait_id)

        self._add_automator_cmd(hplc_inst, start_wait_cmd, [],
                {'condition' : 'status', 'inst_conds': start_conds })
        self._add_automator_cmd(hplc_inst, 'equilibrate', [], equil_settings)
        self._add_automator_cmd(hplc_inst, finish_wait_cmd, [],
            {'condition' : 'status', 'inst_conds': finish_conds})

        if num_paths == 1:
            self._add_automator_cmd('exp', start_wait_cmd, [],
                {'condition' : 'status', 'inst_conds': start_conds})
            self._add_automator_cmd('exp', finish_wait_cmd, [],
                {'condition' : 'status', 'inst_conds': finish_conds})

        if equil_coflow:
            wait_id = self.automator.get_wait_id()
            equil_wait_cmd = 'wait_sync_{}'.format(wait_id)

            self._add_automator_cmd('coflow', start_wait_cmd, [],
                {'condition' : 'status', 'inst_conds': start_conds})

            self._add_automator_cmd('coflow', 'change_buf', [],
                {'buffer_pos': cmd_info['coflow_buf_pos']})

            if cmd_info['coflow_restart']:
                self._add_automator_cmd('coflow', equil_wait_cmd, [],
                    {'condition' : 'status', 'inst_conds': [[hplc_inst,
                    [finish_wait_cmd,]],]})
                self._add_automator_cmd('coflow', 'start', [],
                    {'flow_rate': cmd_info['coflow_rate']})

            self._add_automator_cmd('coflow', finish_wait_cmd, [],
                {'condition' : 'status', 'inst_conds': finish_conds})

        self._post_initialize_cmd()

class SwitchPumpsCommand(AutoCommand):
    """
    A command for switching the dual HPLC pumps
    """
    def __init__(self, *args, **kwargs):
        AutoCommand.__init__(self, *args, **kwargs)

    def _initialize_cmd(self, cmd_info):
        self.auto_names = []
        self.auto_ids = []

        hplc_inst = cmd_info['inst']
        coflow_equil = cmd_info['coflow_equil']

        switch_settings = {
            'purge_rate'    : cmd_info['purge_rate'],
            'purge_volume'  : cmd_info['purge_volume'],
            'purge_accel'   : cmd_info['purge_accel'],
            'restore_flow_after_switch' : cmd_info['restore_flow_after_switch'],
            'switch_with_sample'    : cmd_info['switch_with_sample'],
            'stop_flow1'    : cmd_info['stop_flow1'],
            'stop_flow2'    : cmd_info['stop_flow2'],
            'purge_active'  : cmd_info['purge_active'],
            'flow_path'     : cmd_info['flow_path'],
            }

        num_paths = cmd_info['num_flow_paths']

        switch_wait_id = self.automator.get_wait_id()

        switch_wait_cmd = 'wait_sync_{}'.format(switch_wait_id)

        switch_inst_conds = [['{}_pump{}'.format(hplc_inst, i+1), [switch_wait_cmd,]]
            for i in range(num_paths)]

        if coflow_equil:
            switch_inst_conds.append(['coflow', [switch_wait_cmd,]])

        for i in range(num_paths):
            cmd_name = '{}_pump{}'.format(hplc_inst, i+1)

            self._add_automator_cmd(cmd_name, switch_wait_cmd, [],
                {'condition': 'status', 'inst_conds': switch_inst_conds})

        cmd_name = '{}_pump{}'.format(hplc_inst, cmd_info['flow_path'])

        self._add_automator_cmd(cmd_name, 'switch_pumps', [], switch_settings)

        finish_wait_id = self.automator.get_wait_id()
        finish_wait_cmd = 'wait_sync_{}'.format(finish_wait_id)

        finish_inst_conds = [['{}_pump{}'.format(hplc_inst, i+1), [finish_wait_cmd,]]
            for i in range(num_paths)]

        if coflow_equil:
            finish_inst_conds.append(['coflow', [finish_wait_cmd,]])

        for i in range(num_paths):
            cmd_name = '{}_pump{}'.format(hplc_inst, i+1)

            self._add_automator_cmd(cmd_name, finish_wait_cmd, [],
                {'condition': 'status', 'inst_conds': finish_inst_conds})

        if coflow_equil:
            self._add_automator_cmd('coflow', switch_wait_cmd, [],
                {'condition': 'status', 'inst_conds': switch_inst_conds})

            self._add_automator_cmd('coflow', 'change_buf', [],
                {'buffer_pos': cmd_info['coflow_buf_pos']})

            if cmd_info['coflow_restart']:
                self._add_automator_cmd('coflow', 'start', [],
                    {'flow_rate': cmd_info['coflow_rate']})

            self._add_automator_cmd(cmd_name, finish_wait_cmd, [],
                {'condition': 'status', 'inst_conds': finish_inst_conds})

        self._post_initialize_cmd()

class ExposureCommand(AutoCommand):
    """
    A command for running a SEC sample and collecting SAXS data
    """
    def __init__(self, *args, **kwargs):
        AutoCommand.__init__(self, *args, **kwargs)

    def _initialize_cmd(self, cmd_info):
        """
        Notes:
        Should synchronize start and finish of exp, hplc, and coflow
        """
        # Something like this. Arguments need refining, needs testing


        finish_wait_id = self.automator.get_wait_id()
        finish_wait_cmd = 'wait_sync_{}'.format(finish_wait_id)
        finish_conds = [['exp', [finish_wait_cmd,]],]

        self._add_automator_cmd('exp', 'expose', [], cmd_info)
        self._add_automator_cmd('exp', finish_wait_cmd, [], {'condition': 'status',
            'inst_conds': finish_conds})

        self._post_initialize_cmd()

class AutoPanel(wx.Panel):
    """
    This creates the automator panel.
    """
    def __init__(self, settings, *args, **kwargs):
        """
        Initializes the automator panel. Accepts the usual wx.Panel arguments plus
        the following.
        """
        wx.Panel.__init__(self, *args, **kwargs)

        self.settings = settings

        if self.settings['automator_thread'] is None:
            self.automator = Automator()
            self.automator.start()
            self.settings['automator_thread'] = self.automator
        else:
            self.automator = self.settings['automator_thread']

        self._create_layout()
        self._init_values()

        self.SetMinSize(self._FromDIP((1100, 800)))

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _init_values(self):
        for inst, inst_settings in self.settings['instruments'].items():
            if inst.startswith('hplc'):
                num_paths = inst_settings['num_paths']

                for i in range(num_paths):
                    name = '{}_pump{}'.format(inst, i+1)
                    self.automator.add_control(name, name,
                        inst_settings['automator_callback'])
            else:
                self.automator.add_control(inst, inst,
                    inst_settings['automator_callback'])

    def _create_layout(self):

        ctrl_parent = self

        self.status_panel = AutoStatusPanel(self.settings, ctrl_parent)
        self.auto_list_panel = AutoListPanel(self.settings, ctrl_parent)

        self.top_sizer = wx.BoxSizer(wx.VERTICAL)
        self.top_sizer.Add(self.status_panel, border=self._FromDIP(5),
            flag=wx.EXPAND|wx.ALL)
        self.top_sizer.Add(self.auto_list_panel, proportion=1,
            border=self._FromDIP(5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM)

        self.SetSizer(self.top_sizer)

    def on_exit(self):
        self.automator.stop()
        self.automator.join()
        self.status_panel.status_timer.Stop()

class AutoStatusPanel(wx.Panel):
    def __init__(self, settings, *args, **kwargs):
        wx.Panel.__init__(self, *args, **kwargs)

        self.settings = settings

        self._create_layout()
        self._init_values()

        self.status_timer = wx.Timer()
        self.status_timer.Bind(wx.EVT_TIMER, self._on_status_timer)
        self.status_timer.Start(5000)

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _init_values(self):
        # Initialize automator controls

        self.automator = self.settings['automator_thread']

        state = self.automator.get_automator_state()

        if state == 'run':
            self.resume_btn.Disable()
            self.automator_state.SetLabel('Running')
        else:
            self.pause_btn.Disable()
            self.automator_state.SetLabel('Paused')

        self.automator.add_on_state_change_callback(self._on_state_change)

    def _create_layout(self):

        status_box = wx.StaticBox(self, label='Status')

        ctrl_parent = status_box

        queue_status_box = wx.StaticBox(ctrl_parent, label='Automator')

        self.automator_state = wx.StaticText(queue_status_box,
            size=self._FromDIP((60,-1)), style=wx.ST_NO_AUTORESIZE)

        auto_status_sizer = wx.StaticBoxSizer(queue_status_box, wx.HORIZONTAL)
        auto_status_sizer.Add(wx.StaticText(queue_status_box, label='Queue status:'),
            flag=wx.ALL|wx.ALIGN_CENTER_VERTICAL, border=self._FromDIP(5))
        auto_status_sizer.Add(self.automator_state, border=self._FromDIP(5),
            flag=wx.TOP|wx.BOTTOM|wx.RIGHT|wx.ALIGN_CENTER_VERTICAL)

        self.pause_btn = wx.Button(queue_status_box, label='Pause')
        self.resume_btn = wx.Button(queue_status_box, label='Resume')
        self.stop_btn = wx.Button(queue_status_box, label='Stop current items')

        self.pause_btn.Bind(wx.EVT_BUTTON, self._on_pause_queue)
        self.resume_btn.Bind(wx.EVT_BUTTON, self._on_resume_queue)
        self.stop_btn.Bind(wx.EVT_BUTTON, self._on_stop_queue)

        auto_status_sizer.Add(self.pause_btn, border=self._FromDIP(5),
            flag=wx.TOP|wx.BOTTOM|wx.RIGHT|wx.ALIGN_CENTER_VERTICAL)
        auto_status_sizer.Add(self.resume_btn, border=self._FromDIP(5),
            flag=wx.TOP|wx.BOTTOM|wx.RIGHT|wx.ALIGN_CENTER_VERTICAL)
        auto_status_sizer.Add(self.stop_btn, border=self._FromDIP(5),
            flag=wx.TOP|wx.BOTTOM|wx.RIGHT|wx.ALIGN_CENTER_VERTICAL)


        if 'hplc' in self.settings['instruments']:
            num_paths = self.settings['instruments']['hplc']['num_paths']

            hplc_status_box = wx.StaticBox(ctrl_parent, label='HPLC')

            hplc_stop_btn = wx.Button(hplc_status_box, label='Stop')

            self.hplc_flow_path = wx.StaticText(hplc_status_box, size=self._FromDIP((60,-1)),
                style=wx.ST_NO_AUTORESIZE)
            self.hplc_state = wx.StaticText(hplc_status_box, size=self._FromDIP((80,-1)),
                style=wx.ST_NO_AUTORESIZE)
            self.hplc_runtime = wx.StaticText(hplc_status_box, size=self._FromDIP((60,-1)),
                style=wx.ST_NO_AUTORESIZE)
            self.pump1_state = wx.StaticText(hplc_status_box, size=self._FromDIP((100,-1)),
                style=wx.ST_NO_AUTORESIZE)
            self.pump1_fr = wx.StaticText(hplc_status_box, size=self._FromDIP((60,-1)),
                style=wx.ST_NO_AUTORESIZE)
            self.pump1_pressure = wx.StaticText(hplc_status_box, size=self._FromDIP((60,-1)),
                style=wx.ST_NO_AUTORESIZE)

            num_cols = 11

            if num_paths == 2:
                self.pump2_state = wx.StaticText(hplc_status_box, size=self._FromDIP((100,-1)),
                    style=wx.ST_NO_AUTORESIZE)
                self.pump2_fr = wx.StaticText(hplc_status_box, size=self._FromDIP((60,-1)),
                    style=wx.ST_NO_AUTORESIZE)
                self.pump2_pressure = wx.StaticText(hplc_status_box, size=self._FromDIP((60,-1)),
                    style=wx.ST_NO_AUTORESIZE)

                num_cols = 10

            hplc_sub_status_sizer = wx.FlexGridSizer(cols=num_cols, vgap=self._FromDIP(5),
                hgap=self._FromDIP(5))
            hplc_sub_status_sizer.Add(wx.StaticText(hplc_status_box, label='Status:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            hplc_sub_status_sizer.Add(self.hplc_state, flag=wx.ALIGN_CENTER_VERTICAL)
            hplc_sub_status_sizer.Add(wx.StaticText(hplc_status_box, label='Runtime (min):'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            hplc_sub_status_sizer.Add(self.hplc_runtime, flag=wx.ALIGN_CENTER_VERTICAL)
            hplc_sub_status_sizer.Add(wx.StaticText(hplc_status_box, label='Pump1:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            hplc_sub_status_sizer.Add(self.pump1_state, flag=wx.ALIGN_CENTER_VERTICAL)
            hplc_sub_status_sizer.Add(wx.StaticText(hplc_status_box, label='Pump1 flow (ml/min):'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            hplc_sub_status_sizer.Add(self.pump1_fr, flag=wx.ALIGN_CENTER_VERTICAL)
            hplc_sub_status_sizer.Add(wx.StaticText(hplc_status_box, label='Pump1 pressure (bar):'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            hplc_sub_status_sizer.Add(self.pump1_pressure, flag=wx.ALIGN_CENTER_VERTICAL)

            if num_paths == 2:
                hplc_sub_status_sizer.AddSpacer(1)
                hplc_sub_status_sizer.Add(hplc_stop_btn, flag=wx.ALIGN_CENTER_VERTICAL)
                hplc_sub_status_sizer.Add(wx.StaticText(hplc_status_box, label='Flow path:'),
                    flag=wx.ALIGN_CENTER_VERTICAL)
                hplc_sub_status_sizer.Add(self.hplc_flow_path, flag=wx.ALIGN_CENTER_VERTICAL)
                hplc_sub_status_sizer.Add(wx.StaticText(hplc_status_box, label='Pump2:'),
                    flag=wx.ALIGN_CENTER_VERTICAL)
                hplc_sub_status_sizer.Add(self.pump2_state, flag=wx.ALIGN_CENTER_VERTICAL)
                hplc_sub_status_sizer.Add(wx.StaticText(hplc_status_box, label='Pump2 flow (ml/min):'),
                    flag=wx.ALIGN_CENTER_VERTICAL)
                hplc_sub_status_sizer.Add(self.pump2_fr, flag=wx.ALIGN_CENTER_VERTICAL)
                hplc_sub_status_sizer.Add(wx.StaticText(hplc_status_box, label='Pump2 pressure (bar):'),
                    flag=wx.ALIGN_CENTER_VERTICAL)
                hplc_sub_status_sizer.Add(self.pump2_pressure, flag=wx.ALIGN_CENTER_VERTICAL)

                hplc_sizer = wx.StaticBoxSizer(hplc_status_box, wx.HORIZONTAL)
                hplc_sizer.Add(hplc_sub_status_sizer, proportion=1, flag=wx.ALL,
                    border=self._FromDIP(5))



        if 'exp' in self.settings['instruments']:
            exp_status_box  = wx.StaticBox(ctrl_parent, label='Exposure')

            exp_stop_btn = wx.Button(exp_status_box, label='Stop Exposure')

            self.exp_status = wx.StaticText(exp_status_box, size=self._FromDIP((80,-1)),
                style=wx.ST_NO_AUTORESIZE)
            self.exp_runtime = wx.StaticText(exp_status_box, size=self._FromDIP((60,-1)),
                style=wx.ST_NO_AUTORESIZE)

            if 'coflow' in self.settings['instruments']:
                coflow_stop_btn = wx.Button(exp_status_box, label='Stop Coflow')

                self.coflow_status = wx.StaticText(exp_status_box, size=self._FromDIP((100,-1)),
                    style=wx.ST_NO_AUTORESIZE)
                self.coflow_fr = wx.StaticText(exp_status_box, size=self._FromDIP((60,-1)),
                    style=wx.ST_NO_AUTORESIZE)


            exp_sub_sizer1 = wx.BoxSizer(wx.HORIZONTAL)
            exp_sub_sizer1.Add(wx.StaticText(exp_status_box, label='Status:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            exp_sub_sizer1.Add(self.exp_status, border=self._FromDIP(5),
                flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)
            exp_sub_sizer1.Add(wx.StaticText(exp_status_box, label='Runtime (min):'),
                flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)
            exp_sub_sizer1.Add(self.exp_runtime, border=self._FromDIP(5),
                flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)
            exp_sub_sizer1.Add(exp_stop_btn, border=self._FromDIP(5),
                flag=wx.ALIGN_CENTER_VERTICAL)

            exp_sizer = wx.StaticBoxSizer(exp_status_box, wx.HORIZONTAL)
            exp_sizer.Add(exp_sub_sizer1, flag=wx.ALL, border=self._FromDIP(5))

            if 'coflow' in self.settings['instruments']:
                exp_sub_sizer2 = wx.BoxSizer(wx.HORIZONTAL)
                exp_sub_sizer2.Add(wx.StaticText(exp_status_box, label='Coflow status:'),
                    flag=wx.ALIGN_CENTER_VERTICAL)
                exp_sub_sizer2.Add(self.coflow_status, border=self._FromDIP(5),
                    flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)
                exp_sub_sizer2.Add(wx.StaticText(exp_status_box, label='Setpoint (ml/min):'),
                    flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)
                exp_sub_sizer2.Add(self.coflow_fr, border=self._FromDIP(5),
                    flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)
                exp_sub_sizer2.Add(coflow_stop_btn, border=self._FromDIP(5),
                    flag=wx.ALIGN_CENTER_VERTICAL)


                exp_sizer.Add(exp_sub_sizer2, flag=wx.ALL, border=self._FromDIP(5))


        self.top_sizer = wx.StaticBoxSizer(status_box, wx.VERTICAL)
        self.top_sizer.Add(auto_status_sizer, flag=wx.EXPAND|wx.ALL,
            border=self._FromDIP(5))

        if 'hplc' in self.settings['instruments']:
            self.top_sizer.Add(hplc_sizer, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM,
                border=self._FromDIP(5))

        if 'exp' in self.settings['instruments']:
            self.top_sizer.Add(exp_sizer, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM,
                border=self._FromDIP(5))

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

    def _on_status_timer(self, evt):
        wx.CallAfter(self._get_status)

    def _get_status(self):
        if 'hplc' in self.settings['instruments']:
            hplc_callback = self.settings['instruments']['hplc']['automator_callback']
            num_paths = self.settings['instruments']['hplc']['num_paths']

            status, success = hplc_callback('full_status', [], {})

            self.hplc_flow_path.SetLabel(status['flow_path'])
            self.hplc_state.SetLabel(status['state'])
            self.hplc_runtime.SetLabel(status['runtime'])
            self.pump1_state.SetLabel(status['pump1_state'])
            self.pump1_fr.SetLabel(status['pump1_fr'])
            self.pump1_pressure.SetLabel(status['pump1_pressure'])

            if num_paths == 2:
                self.pump2_state.SetLabel(status['pump2_state'])
                self.pump2_fr.SetLabel(status['pump2_fr'])
                self.pump2_pressure.SetLabel(status['pump2_pressure'])


        if 'exp' in self.settings['instruments']:
            exp_callback = self.settings['instruments']['exp']['automator_callback']
            status, success = exp_callback('full_status', [], {})

            self.exp_status.SetLabel(status['status'])
            self.exp_runtime.SetLabel(status['runtime'])

            if 'coflow' in self.settings['instruments']:
                coflow_callback = self.settings['instruments']['coflow']['automator_callback']
                status, success = coflow_callback('full_status', [], {})

                self.coflow_status.SetLabel(status['status'])
                self.coflow_fr.SetLabel(status['fr'])


class AutoSettings(scrolled.ScrolledPanel):
    def __init__(self, auto_panel, *args, **kwargs):
        scrolled.ScrolledPanel.__init__(self, *args, **kwargs)

        self.SetBackgroundColour('White')

        self.auto_panel = auto_panel
        self._sec_saxs_settings = copy.copy(default_sec_saxs_settings)
        self._standalone_exp_settings = copy.copy(default_standalone_exp_settings)

        self.ctrl_ids = {
            'sec_sample'    : {},
            'exposure'      : {},
            }

        for key in self._sec_saxs_settings.keys():
            self.ctrl_ids['sec_saxs'][key] = wx.NewIdRef()

        for key in self._standalone_exp_settings.keys():
            self.ctrl_ids['exposure'][key] = wx.NewIdRef()

        self._create_layout()
        # self._init_values()

        self.SetupScrolling()

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def on_collapse(self, event):
        self.Layout()
        self.Refresh()
        self.SendSizeEvent()

    def _create_layout(self):
        top_level = self
        parent = self

        if 'hplc' in self.auto_panel.settings['instruments']:
            hplc_panel = self.auto_panel.settings['instruments']['hplc']['hplc_panel']
            default_inj_settings = hplc_panel.get_default_sample_settings()
            acq_methods = default_inj_settings['all_acq_methods']
            sample_methods = default_inj_settings['all_sample_methods']
            sample_methods.insert(0, 'None')
            inst = self.auto_panel.settings['hplc_inst']
            num_flow_paths = self.auto_panel.settings['instruments'][inst]['num_paths']

        else:
            acq_methods = []
            sample_methods = []
            num_flow_paths = 1

        self.sec_saxs_panel = make_sec_saxs_info_panel(top_level, parent,
            self.ctrl_ids['sec_sample'], 'vert', num_flow_paths, acq_methods,
            sample_methods, read_only=True)

        self.exp_panel = make_standalone_exp_panel(top_level, parent,
            self.ctrl_ids['exposure'], 'vert', read_only=True)

        self.top_sizer = wx.BoxSizer(wx.VERTICAL)
        self.top_sizer.Add(self.sec_saxs_panel, flag=wx.EXPAND, proportion=1)
        self.top_sizer.Add(self.exp_panel, flag=wx.EXPAND, proportion=1)

        self.top_sizer.Hide(self.sec_saxs_panel, recursive=True)
        self.top_sizer.Hide(self.exp_panel, recursive=True)

        self.SetSizer(self.top_sizer)

    def on_item_selection(self, settings):
        item_type = settings['item_type']
        for key, c_id in self.ctrl_ids[item_type].items():
            default_val = settings[key]
            ctrl = self.FindWindowById(c_id)

            if ctrl is not None:
                if isinstance(ctrl, wx.Choice):
                    ctrl.SetStringSelection(str(default_val))
                else:
                    try:
                        ctrl.SetValue(str(default_val))
                    except TypeError:
                        ctrl.SetValue(default_val)

        if item_type == 'sec_sample':
            self.top_sizer.Show(self.sec_saxs_panel, recursive=True)
            self.top_sizer.Hide(self.exp_panel, recursive=True)

        elif item_type == 'exposure':
            self.top_sizer.Hide(self.sec_saxs_panel, recursive=True)
            self.top_sizer.Show(self.exp_panel, recursive=True)

default_sec_saxs_settings = {
    # General parameters
    'item_type'     : 'sec_sample',
    'notes'         : '',
    'conc'          : '',
    'buf'           : '',
    'inst'          : '',
    'sample_name'   : '',
    'column'        : 'Superdex 200 10/300 Increase',
    'temp'          : '20',

    # Injection parameters
    'acq_method'    : '',
    'sample_loc'    : '',
    'inj_vol'       : 0.,
    'flow_rate'     : 0.,
    'elution_vol'   : 0.,
    'flow_accel'    : 0.,
    'pressure_lim'  : 0.,
    'result_path'   : '',
    'sp_method'     : '',
    'wait_for_flow_ramp': True,
    'settle_time'   : 0.,
    'flow_path'     : 1,
    'stop_flow'     : False,

    # Exposure parameters
    'frames_by_elut': True,
    'num_frames'    : 0,
    'exp_time'      : 0.,
    'exp_period'    : 0.,
    'data_dir'      : '',
    'filename'      : '',
    'wait_for_trig' : True,
    'num_trig'      : 0,
    #Not used, for completeness
    'struck_measurement_time' : 0.,

    #Coflow parameters
    'coflow_from_fr': True,
    'start_coflow'  : True,
    'stop_coflow'   : False,
    'coflow_fr'     : 0.,
    }



default_standalone_exp_settings = {
    # General parameters
    'item_type'     : 'exposure',
    'inst'          : 'exp',
    'notes'         : '',
    'conc'          : '',
    'buf'           : '',
    'sample_name'   : '',
    'column'        : 'Superdex 200 10/300 Increase',
    'temp'          : '20',
    'inj_vol'       : '',
    'exp_type'      : 'SEC-SAXS',

    # Exposure parameters
    'num_frames'    : 0,
    'exp_time'      : 0.,
    'exp_period'    : 0.,
    'data_dir'      : '',
    'filename'      : '',
    'wait_for_trig' : True,
    'num_trig'      : 0,
    #Not used, for completeness
    'struck_measurement_time' : 0.,
    }


def create_info_sizer(layout_settings, top_level, parent, read_only=False):
    top_sizer = wx.GridBagSizer(vgap=top_level._FromDIP(5), hgap=top_level._FromDIP(5))

    for row, item in enumerate(layout_settings.values()):
        label = item[0]
        myId = item[1]
        itemType = item[2]

        if itemType == 'choice':
            labeltxt = wx.StaticText(parent, -1, label)
            ctrl = wx.Choice(parent, myId, choices=item[3])
            ctrl.SetSelection(0)

            if read_only:
                ctrl.Disable()

            top_sizer.Add(labeltxt, (row, 0), flag=wx.ALIGN_CENTER_VERTICAL)
            top_sizer.Add(ctrl, (row, 1), flag=wx.ALIGN_CENTER_VERTICAL)

        elif itemType == 'text' or itemType == 'int' or itemType =='float':
            labeltxt = wx.StaticText(parent, -1, label)

            if itemType == 'int':
                valid = utils.CharValidator('int')
            elif itemType == 'float':
                valid=utils.CharValidator('float')
            else:
                valid = None

            if valid:
                ctrl = wx.TextCtrl(parent, myId, '', size=top_level._FromDIP((100,-1)),
                    validator=valid)
            else:
                ctrl = wx.TextCtrl(parent, myId, '', size=top_level._FromDIP((100,-1)))

            if read_only:
                ctrl.SetEditable(False)

            top_sizer.Add(labeltxt, (row, 0), flag=wx.ALIGN_CENTER_VERTICAL)
            top_sizer.Add(ctrl, (row, 1), flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)

        elif itemType == 'bool':
            ctrl = wx.CheckBox(parent, myId, label)

            if read_only:
                ctrl.Disable()

            top_sizer.Add(ctrl, (row, 0), span=(1,2),
                flag=wx.ALIGN_CENTER_VERTICAL)

    top_sizer.AddGrowableCol(1)

    return top_sizer

def make_sec_saxs_info_panel(top_level, parent, ctrl_ids, cmd_sizer_dir,
    num_flow_paths, acq_methods, sample_methods, read_only=False):
    ################ Metadata #################
    column_choices = ['Superdex 200 10/300 Increase', 'Superdex 75 10/300 Increase',
        'Superose 6 10/300 Increase', 'Superdex 200 5/150 Increase',
        'Superdex 75 5/150 Increase', 'Superose 6 5/150 Increase',
        'Superdex 200 10/300', 'Superdex 75 10/300', 'Superose 6 10/300',
        'Superdex 200 5/150', 'Superdex 75 5/150', 'Superose 6 5/150',
        'Wyatt 010S5', 'Wyatt 015S5', 'Wyatt 030S5', 'Capto HiRes Q 5/50',
        'Capto HiRes S 5/50', 'Other']

    metadata_settings = {
        'sample_name'   : ['Sample:', ctrl_ids['sample_name'], 'text'],
        'buf'           : ['Buffer:', ctrl_ids['buf'], 'text'],
        'temp'          : ['Temperature [C]:', ctrl_ids['temp'], 'float'],
        'conc'          : ['Concentration [mg/ml]:', ctrl_ids['conc'], 'float'],
        'column'        : ['Column:', ctrl_ids['column'], 'choice', column_choices],
        }

    metadata_box = wx.StaticBox(parent, label='Metadata')
    md_sizer1 = create_info_sizer(metadata_settings, top_level, metadata_box,
        read_only)

    notes = wx.TextCtrl(metadata_box, ctrl_ids['notes'],
        style=wx.TE_MULTILINE, size=top_level._FromDIP((100, 100)))

    if read_only:
        notes.SetEditable(False)

    md_sizer2 = wx.BoxSizer(wx.HORIZONTAL)
    md_sizer2.Add(wx.StaticText(metadata_box, label='Notes:'),
        border=top_level._FromDIP(5), flag=wx.TOP|wx.BOTTOM|wx.LEFT)
    md_sizer2.Add(notes, proportion=1, border=top_level._FromDIP(5),
        flag=wx.EXPAND|wx.ALL)

    metadata_sizer = wx.StaticBoxSizer(metadata_box, wx.VERTICAL)
    metadata_sizer.Add(md_sizer1, flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT,
        border=top_level._FromDIP(5))
    metadata_sizer.Add(md_sizer2, proportion=1, flag=wx.EXPAND|wx.ALL,
        border=top_level._FromDIP(5))

    ################ HPLC #################
    fp_choices = ['{}'.format(i+1) for i in
        range(int(num_flow_paths))]

    hplc_settings = {
        'sample_loc'    : ['Sample location:', ctrl_ids['sample_loc'], 'text'],
        'inj_vol'       : ['Injection volume [uL]:', ctrl_ids['inj_vol'], 'float'],
        'flow_rate'     : ['Flow rate [ml/min]:', ctrl_ids['flow_rate'], 'float'],
        'elution_vol'   : ['Elution volume [ml]:', ctrl_ids['elution_vol'], 'float'],
        'flow_path'     : ['Flow path:', ctrl_ids['flow_path'], 'choice', fp_choices],
        }

    hplc_adv_settings = {
        'acq_method'    : ['Acquisition method:', ctrl_ids['acq_method'],
                            'choice', acq_methods],
        'sp_method'     : ['Sample prep. method:', ctrl_ids['sp_method'],
                            'choice', sample_methods],
        'flow_accel'    : ['Flow acceleration [ml/min^2]:', ctrl_ids['flow_accel'], 'float'],
        'pressure_lim'  : ['Max pressure [bar]:', ctrl_ids['pressure_lim'], 'float'],
        'wait_for_flow_ramp' : ['Wait for flow ramp', ctrl_ids['wait_for_flow_ramp'], 'bool'],
        'settle_time'   : ['Settle time [s]:', ctrl_ids['settle_time'], 'float'],
        'result_path'   : ['Result path:', ctrl_ids['result_path'], 'text'],
        'stop_flow'     : ['Stop flow after elution', ctrl_ids['stop_flow'], 'bool'],
        }

    hplc_box = wx.StaticBox(parent, label='HPLC Settings')

    hplc_adv_pane = wx.CollapsiblePane(hplc_box, label="Advanced Settings")
    hplc_adv_pane.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, top_level.on_collapse)
    hplc_adv_win = hplc_adv_pane.GetPane()

    hplc_sizer1 = create_info_sizer(hplc_settings, top_level, hplc_box, read_only)
    hplc_sizer2 = create_info_sizer(hplc_adv_settings, top_level, hplc_adv_win,
        read_only)

    hplc_adv_win.SetSizer(hplc_sizer2)
    hplc_adv_pane.Collapse()

    hplc_sizer = wx.StaticBoxSizer(hplc_box, wx.VERTICAL)
    hplc_sizer.Add(hplc_sizer1, flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT,
        border=top_level._FromDIP(5))
    hplc_sizer.Add(hplc_adv_pane, flag=wx.EXPAND|wx.ALL, border=top_level._FromDIP(5))


    ################ Exposure #################
    exp_settings = {
        'filename'      : ['File prefix:', ctrl_ids['filename'], 'text'],
        'exp_time'      : ['Exposure time [s]:', ctrl_ids['exp_time'], 'float'],
        'exp_period'    : ['Exposure period [s]:', ctrl_ids['exp_period'], 'float'],
        }

    exp_adv_settings = {
        'frames_by_elut': ['Set number of frames from elution time',
                            ctrl_ids['frames_by_elut'], 'bool'],
        'num_frames'    : ['Number of frames:', ctrl_ids['num_frames'], 'int'],
        'wait_for_trig' : ['Wait for external trigger', ctrl_ids['wait_for_trig'], 'bool'],
        'num_trig'      : ['Number of triggers:', ctrl_ids['num_trig'], 'int'],
        }

    exp_box = wx.StaticBox(parent, label='Exposure Settings')

    exp_adv_pane = wx.CollapsiblePane(exp_box, label="Advanced Settings")
    exp_adv_pane.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, top_level.on_collapse)
    exp_adv_win = exp_adv_pane.GetPane()

    exp_sizer1 = create_info_sizer(exp_settings, top_level, exp_box, read_only)
    exp_sizer2 = create_info_sizer(exp_adv_settings, top_level, exp_adv_win,
        read_only)

    exp_adv_win.SetSizer(exp_sizer2)
    exp_adv_pane.Collapse()

    exp_sizer = wx.StaticBoxSizer(exp_box, wx.VERTICAL)
    exp_sizer.Add(exp_sizer1, flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT,
        border=top_level._FromDIP(5))
    exp_sizer.Add(exp_adv_pane, flag=wx.EXPAND|wx.ALL, border=top_level._FromDIP(5))


    ################ Coflow #################
    coflow_settings = {
        'coflow_from_fr': ['Set coflow flow from HPLC flow rate',
                            ctrl_ids['coflow_from_fr'], 'bool'],
        'start_coflow'  : ['Start coflow automatically',
                            ctrl_ids['start_coflow'], 'bool'],
        }

    coflow_adv_settings = {
        'stop_cloflow'  : ['Stop coflow after exposure',
                            ctrl_ids['stop_coflow'], 'bool'],
        'coflow_fr'     : ['Coflow flow rate [mL/min]:',
                            ctrl_ids['coflow_fr'], 'float'],
        }

    coflow_box = wx.StaticBox(parent, label='Coflow Settings')

    coflow_adv_pane = wx.CollapsiblePane(coflow_box, label="Advanced Settings")
    coflow_adv_pane.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, top_level.on_collapse)
    coflow_adv_win = coflow_adv_pane.GetPane()

    coflow_sizer1 = create_info_sizer(coflow_settings, top_level, coflow_box,
        read_only)
    coflow_sizer2 = create_info_sizer(coflow_adv_settings, top_level,
        coflow_adv_win, read_only)

    coflow_adv_win.SetSizer(coflow_sizer2)
    coflow_adv_pane.Collapse()

    coflow_sizer = wx.StaticBoxSizer(coflow_box, wx.VERTICAL)
    coflow_sizer.Add(coflow_sizer1, flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT,
        border=top_level._FromDIP(5))
    coflow_sizer.Add(coflow_adv_pane, flag=wx.EXPAND|wx.ALL, border=top_level._FromDIP(5))

    exp_coflow_sizer = wx.BoxSizer(wx.VERTICAL)
    exp_coflow_sizer.Add(exp_sizer, flag=wx.EXPAND)
    exp_coflow_sizer.Add(coflow_sizer, flag=wx.TOP|wx.EXPAND, border=top_level._FromDIP(5))

    if cmd_sizer_dir == 'horiz':
        cmd_sizer=wx.BoxSizer(wx.HORIZONTAL)
        cmd_sizer.Add(metadata_sizer, proportion=1, flag=wx.ALL|wx.EXPAND,
            border=top_level._FromDIP(5))
        cmd_sizer.Add(hplc_sizer, flag=wx.TOP|wx.RIGHT|wx.BOTTOM|wx.EXPAND,
            border=top_level._FromDIP(5))
        cmd_sizer.Add(exp_coflow_sizer, flag=wx.TOP|wx.RIGHT|wx.BOTTOM|wx.EXPAND,
            border=top_level._FromDIP(5))
    else:
        cmd_sizer=wx.BoxSizer(wx.VERTICAL)
        cmd_sizer.Add(metadata_sizer, flag=wx.ALL|wx.EXPAND,
            border=top_level._FromDIP(5))
        cmd_sizer.Add(hplc_sizer, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM|wx.EXPAND,
            border=top_level._FromDIP(5))
        cmd_sizer.Add(exp_coflow_sizer, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM|wx.EXPAND,
            border=top_level._FromDIP(5))

    return cmd_sizer


def make_standalone_exp_panel(top_level, parent, ctrl_ids, cmd_sizer_dir,
    read_only=False):
    ################ Metadata #################
    column_choices = ['Superdex 200 10/300 Increase', 'Superdex 75 10/300 Increase',
        'Superose 6 10/300 Increase', 'Superdex 200 5/150 Increase',
        'Superdex 75 5/150 Increase', 'Superose 6 5/150 Increase',
        'Superdex 200 10/300', 'Superdex 75 10/300', 'Superose 6 10/300',
        'Superdex 200 5/150', 'Superdex 75 5/150', 'Superose 6 5/150',
        'Wyatt 010S5', 'Wyatt 015S5', 'Wyatt 030S5', 'Capto HiRes Q 5/50',
        'Capto HiRes S 5/50', 'Other']

    exp_choices = ['AF4-MALS-SAXS', 'Batch mode SAXS', 'IEC-SAXS',
        'SEC-SAXS', 'SEC-MALS-SAXS', 'TR-SAXS', 'Other']

    metadata_settings = {
        'exp_type'      : ['Experiment type:', ctrl_ids['exp_type'],
                            'choice', exp_choices],
        'sample_name'   : ['Sample:', ctrl_ids['sample_name'], 'text'],
        'buf'           : ['Buffer:', ctrl_ids['buf'], 'text'],
        'inj_vol'       : ['Injection volume [uL]:', ctrl_ids['inj_vol'], 'float'],
        'temp'          : ['Temperature [C]:', ctrl_ids['temp'], 'float'],
        'conc'          : ['Concentration [mg/ml]:', ctrl_ids['conc'], 'float'],
        'column'        : ['Column:', ctrl_ids['column'], 'choice', column_choices],
        }

    metadata_box = wx.StaticBox(parent, label='Metadata')
    md_sizer1 = create_info_sizer(metadata_settings, top_level, metadata_box,
        read_only)

    notes = wx.TextCtrl(metadata_box, ctrl_ids['notes'],
        style=wx.TE_MULTILINE, size=top_level._FromDIP((100, 100)))

    if read_only:
        notes.SetEditable(False)

    md_sizer2 = wx.BoxSizer(wx.HORIZONTAL)
    md_sizer2.Add(wx.StaticText(metadata_box, label='Notes:'),
        border=top_level._FromDIP(5), flag=wx.TOP|wx.BOTTOM|wx.LEFT)
    md_sizer2.Add(notes, proportion=1, border=top_level._FromDIP(5),
        flag=wx.EXPAND|wx.ALL)

    metadata_sizer = wx.StaticBoxSizer(metadata_box, wx.VERTICAL)
    metadata_sizer.Add(md_sizer1, flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT,
        border=top_level._FromDIP(5))
    metadata_sizer.Add(md_sizer2, proportion=1, flag=wx.EXPAND|wx.ALL,
        border=top_level._FromDIP(5))


    ################ Exposure #################
    exp_settings = {
        'filename'      : ['File prefix:', ctrl_ids['filename'], 'text'],
        'exp_time'      : ['Exposure time [s]:', ctrl_ids['exp_time'], 'float'],
        'exp_period'    : ['Exposure period [s]:', ctrl_ids['exp_period'], 'float'],
        'num_frames'    : ['Number of frames:', ctrl_ids['num_frames'], 'int'],
        }

    exp_adv_settings = {
        'wait_for_trig' : ['Wait for external trigger', ctrl_ids['wait_for_trig'], 'bool'],
        'num_trig'      : ['Number of triggers:', ctrl_ids['num_trig'], 'int'],
        }

    exp_box = wx.StaticBox(parent, label='Exposure Settings')

    exp_adv_pane = wx.CollapsiblePane(exp_box, label="Advanced Settings")
    exp_adv_pane.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, top_level.on_collapse)
    exp_adv_win = exp_adv_pane.GetPane()

    exp_sizer1 = create_info_sizer(exp_settings, top_level, exp_box, read_only)
    exp_sizer2 = create_info_sizer(exp_adv_settings, top_level, exp_adv_win,
        read_only)

    exp_adv_win.SetSizer(exp_sizer2)
    exp_adv_pane.Collapse()

    exp_sizer = wx.StaticBoxSizer(exp_box, wx.VERTICAL)
    exp_sizer.Add(exp_sizer1, flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT,
        border=top_level._FromDIP(5))
    exp_sizer.Add(exp_adv_pane, flag=wx.EXPAND|wx.ALL, border=top_level._FromDIP(5))


    if cmd_sizer_dir == 'horiz':
        cmd_sizer=wx.BoxSizer(wx.HORIZONTAL)
        cmd_sizer.Add(exp_sizer, flag=wx.TOP|wx.RIGHT|wx.BOTTOM|wx.EXPAND,
            border=top_level._FromDIP(5))
        cmd_sizer.Add(metadata_sizer, proportion=1, flag=wx.ALL|wx.EXPAND,
            border=top_level._FromDIP(5))
    else:
        cmd_sizer=wx.BoxSizer(wx.VERTICAL)
        cmd_sizer.Add(exp_sizer, flag=wx.ALL|wx.EXPAND,
            border=top_level._FromDIP(5))
        cmd_sizer.Add(metadata_sizer, flag=wx.ALL|wx.EXPAND,
            border=top_level._FromDIP(5))

    return cmd_sizer

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

        self.automator.add_on_error_cmd_callback(self._on_automator_error_callback)
        self.automator.add_on_abort_callback(self._on_automator_abort_callback)

    def _create_layout(self):
        actions_box = wx.StaticBox(self, label='Actions')
        top_list_ctrl = self._create_list_layout(actions_box)
        top_settings_ctrl = self._create_settings_layout(actions_box)

        self.top_sizer = wx.StaticBoxSizer(actions_box, wx.HORIZONTAL)
        self.top_sizer.Add(top_list_ctrl, proportion=5,
            flag=wx.RIGHT|wx.EXPAND, border=self._FromDIP(5))
        self.top_sizer.Add(top_settings_ctrl, proportion=4,
            flag=wx.EXPAND)

        self.SetSizer(self.top_sizer)

    def _create_list_layout(self, parent):
        self.auto_list = AutoList(self._on_add_item_callback,
            self._on_remove_item_callback, self._on_move_item_callback,
            self, parent)

        return self.auto_list

    def _create_settings_layout(self, parent):
        settings_box = wx.StaticBox(parent, label='Settings')
        self.auto_settings = AutoSettings(self, settings_box)

        auto_settings_sizer = wx.StaticBoxSizer(settings_box, wx.VERTICAL)
        auto_settings_sizer.Add(self.auto_settings, proportion=1, flag=wx.EXPAND)

        return auto_settings_sizer

    def _on_add_item_callback(self, item_info):
        item_type = item_info['item_type']

        if item_type == 'sec_sample':
            """
            Check various things, including:
                *   Is there enough buffer to do the run
                *   Do we need to add an instrument switch or an equilibration
                    (should this be checked in the auto list, so that it can add
                    an equilibration item or switch item above this?)
            """
            command = SecSampleCommand(self.automator, item_info)

        elif item_type == 'equilibrate':
            command = EquilibrateCommand(self.automator, item_info)

        elif item_type == 'switch_pumps':
            command = SwitchPumpsCommand(self.automator, item_info)

        elif item_type == 'exposure':
            command = ExposureCommand(self.automator, item_info)

        return command

    def _on_remove_item_callback(self, cmd_list):
        state = self.automator.get_automator_state()

        if state == 'run':
            self.automator.set_automator_state('pause')

        for command in cmd_list:
            command.delete_command()

        if state == 'run':
            self.automator.set_automator_state('run')

    def _on_move_item_callback(self, aid, cmd_name, dist):
        self.automator.reorder_cmd(cmd_name, aid, dist)

    def _on_automator_error_callback(self, aid, cmd_name, inst_name):
        pass # Do something with the errors here

    def _on_automator_abort_callback(self, aid, name):
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
        self._add_action()

    def _add_action(self, settings=None):

        if settings is None:
            actions = []

            if ('hplc' in self.auto_panel.settings['instruments'] and
                'exp' in self.auto_panel.settings['instruments'] and
                'coflow' in self.auto_panel.settings['instruments']):
                actions.extend(['Run SEC-SAXS sample', 'Equilibrate column',
                'Switch pumps'])

            actions.append('----Staff Methods----')

            if 'exp' in self.auto_panel.settings['instruments']:
                actions.extend(['Standalone Exposure'])

            dialog = wx.SingleChoiceDialog(self, 'Pick an action to add:',
                'Pick an action to add to the queue', actions)

            dialog.SetSize(self._FromDIP((300,250)))

            res = dialog.ShowModal()

            if res == wx.ID_OK:
                choice = dialog.GetStringSelection()

            else:
                choice = None

            dialog.Destroy()

        else:
            if settings['item_type'] == 'sec_sample':
                choice = 'Run SEC-SAXS sample'
            elif settings['item_type'] == 'equilibrate':
                choice = 'Equilibrate column'
            elif settings['item_type'] == 'switch_pumps':
                choice = 'Switch pumps'
            elif settings['item_type'] == 'exposure':
                choice = 'Standalone Exposure'

        cmd_settings = self._get_cmd_settings(choice, settings)

        if cmd_settings is not None:

            valid, err_msg = self._validate_cmd(cmd_settings)

            if valid:
                self._add_item(cmd_settings)
            else:
                with wx.MessageDialog(self, err_msg,
                    caption='Action Parameter Errors',
                    style=wx.OK|wx.CANCEL|wx.OK_DEFAULT) as err_dialog:

                    err_dialog.SetOKLabel('Fix errors')

                    ret = err_dialog.ShowModal()

                    if ret == wx.ID_OK:
                        wx.CallAfter(self._add_action, cmd_settings)

    def _get_cmd_settings(self, choice, settings):
        if choice is not None and choice != '----Staff Methods----':
            if choice == 'Run SEC-SAXS sample':
                if settings is None:
                    exp_panel = wx.FindWindowByName('exposure')
                    default_exp_settings, _ = exp_panel.get_exp_values(False)

                    hplc_panel = self.auto_panel.settings['instruments']['hplc']['hplc_panel']
                    default_inj_settings = hplc_panel.get_default_sample_settings()

                    coflow_panel = wx.FindWindowByName('coflow')
                    coflow_fr = coflow_panel.get_flow_rate()
                    try:
                        coflow_fr = float(coflow_fr)
                    except ValueError:
                        coflow_fr = float(coflow_panel.settings['lc_flow_rate'])

                    default_settings = copy.deepcopy(default_sec_saxs_settings)

                    # General parameters
                    default_settings['inst'] = '{}_pump'.format(self.auto_panel.settings['hplc_inst']),

                    # Injection parameters
                    default_settings['acq_method'] = default_inj_settings['acq_method']
                    default_settings['sample_loc'] = default_inj_settings['sample_loc']
                    default_settings['inj_vol'] = default_inj_settings['inj_vol']
                    default_settings['flow_rate'] = default_inj_settings['flow_rate']
                    default_settings['elution_vol'] = default_inj_settings['elution_vol']
                    default_settings['flow_accel'] = default_inj_settings['flow_accel']
                    default_settings['pressure_lim'] = default_inj_settings['pressure_lim']
                    default_settings['result_path'] = default_inj_settings['result_path']
                    default_settings['sp_method'] = default_inj_settings['sp_method']
                    default_settings['wait_for_flow_ramp'] = default_inj_settings['wait_for_flow_ramp']
                    default_settings['settle_time'] = default_inj_settings['settle_time']

                    # Exposure parameters
                    default_settings['num_frames'] = default_exp_settings['num_frames']
                    default_settings['exp_time'] = default_exp_settings['exp_time']
                    default_settings['exp_period'] = default_exp_settings['exp_period']
                    default_settings['data_dir'] = exp_panel.settings['base_data_dir']
                    default_settings['wait_for_trig'] = default_exp_settings['wait_for_trig']
                    default_settings['num_trig'] = default_exp_settings['num_trig']
                    #Not used, for completeness
                    default_settings['struck_measurement_time'] = default_exp_settings['struck_measurement_time']

                    #Coflow parameters
                    default_settings['coflow_fr'] = coflow_fr


                    inst = self.auto_panel.settings['hplc_inst']
                    num_flow_paths = self.auto_panel.settings['instruments'][inst]['num_paths']
                    default_settings['num_flow_paths'] = num_flow_paths

                else:
                    default_settings = settings

                cmd_dialog = SecSampleCmdDialog(self, default_settings,
                    default_inj_settings['all_acq_methods'],
                    default_inj_settings['all_sample_methods'],
                    title='SEC-SAXS Sample Settings')

            elif choice == 'Equilibrate column':
                if settings is None:
                    hplc_panel = self.auto_panel.settings['instruments']['hplc']['hplc_panel']
                    default_equil_settings = hplc_panel.get_default_equil_settings()

                    coflow_panel = wx.FindWindowByName('coflow')
                    coflow_fr = coflow_panel.get_flow_rate()
                    try:
                        coflow_fr = float(coflow_fr)
                    except ValueError:
                        coflow_fr = float(coflow_panel.settings['lc_flow_rate'])

                    default_settings = {
                        # General parameterss
                        'item_type' : 'equilibrate',
                        'buf'       : '',
                        'inst'      : '{}_pump'.format(self.auto_panel.settings['hplc_inst']),

                        # HPLC equilibrate parameters
                        'equil_rate'        : default_equil_settings['equil_rate'],
                        'equil_vol'         : default_equil_settings['equil_vol'],
                        'equil_accel'       : default_equil_settings['equil_accel'],
                        'purge'             : default_equil_settings['purge'],
                        'purge_rate'        : default_equil_settings['purge_rate'],
                        'purge_volume'      : default_equil_settings['purge_vol'],
                        'purge_accel'       : default_equil_settings['purge_accel'],
                        'equil_with_sample' : False,
                        'stop_after_equil'  : default_equil_settings['stop_after_equil'],
                        'flow_path'         : 1,
                        'buffer_position'   : 1,

                        # Coflow equilibrate parameters
                        'coflow_equil'      : True,
                        'coflow_buf_pos'    : 1,
                        'coflow_restart'    : True,
                        'coflow_rate'       : coflow_fr,
                        }

                    inst = self.auto_panel.settings['hplc_inst']
                    num_flow_paths = self.auto_panel.settings['instruments'][inst]['num_paths']
                    default_settings['num_flow_paths'] = num_flow_paths

                    if num_flow_paths == 1:
                        default_settings['coflow_equil'] = True
                    else:
                        default_settings['coflow_equil'] = False

                else:
                    default_settings = settings

                cmd_dialog = EquilibrateDialog(self, default_settings,
                    title='Equilibration Settings', size=self._FromDIP((200,200)))

            elif choice == 'Switch pumps':
                if settings is None:
                    hplc_panel = self.auto_panel.settings['instruments']['hplc']['hplc_panel']
                    default_switch_settings = hplc_panel.get_default_switch_flow_path_settings()

                    coflow_panel = wx.FindWindowByName('coflow')
                    coflow_fr = coflow_panel.get_flow_rate()
                    try:
                        coflow_fr = float(coflow_fr)
                    except ValueError:
                        coflow_fr = float(coflow_panel.settings['lc_flow_rate'])

                    default_settings = {
                        'item_type' : 'switch_pumps',
                        'inst'      : self.auto_panel.settings['hplc_inst'],

                        #Switch parameters
                        'purge_rate'    : default_switch_settings['purge_rate'],
                        'purge_volume'  : default_switch_settings['purge_vol'],
                        'purge_accel'   : default_switch_settings['purge_accel'],
                        'restore_flow_after_switch' : default_switch_settings['restore_flow_after_switch'],
                        'switch_with_sample'    : False,
                        'stop_flow1'    : default_switch_settings['stop_flow1'],
                        'stop_flow2'    : default_switch_settings['stop_flow2'],
                        'purge_active'  : default_switch_settings['purge_active'],
                        'flow_path'     : 1,

                        #Coflow switch parameters
                        'coflow_equil'      : True,
                        'coflow_buf_pos'    : 1,
                        'coflow_restart'    : True,
                        'coflow_rate'       : coflow_fr,
                        }

                    inst = self.auto_panel.settings['hplc_inst']
                    num_flow_paths = self.auto_panel.settings['instruments'][inst]['num_paths']
                    # num_flow_paths = 2
                    default_settings['num_flow_paths'] = num_flow_paths

                else:
                    default_settings = settings

                cmd_dialog = SwitchDialog(self, default_settings,
                    title='Switch Pump Settings')

            if choice == 'Standalone Exposure':
                if settings is None:
                    exp_panel = wx.FindWindowByName('exposure')
                    default_exp_settings, _ = exp_panel.get_exp_values(False)

                    default_settings = copy.deepcopy(default_standalone_exp_settings)
                    default_settings['num_frames'] = default_exp_settings['num_frames']
                    default_settings['exp_time'] = default_exp_settings['exp_time']
                    default_settings['exp_period'] = default_exp_settings['exp_period']
                    default_settings['data_dir'] = exp_panel.settings['base_data_dir']
                    default_settings['wait_for_trig'] = default_exp_settings['wait_for_trig']
                    default_settings['num_trig'] = default_exp_settings['num_trig']
                    #Not used, for completeness
                    default_settings['struck_measurement_time'] = default_exp_settings['struck_measurement_time']

                else:
                    default_settings = settings

                cmd_dialog = ExposureCmdDialog(self, default_settings,
                    title='Standalone Exposure Settings')

            res = cmd_dialog.ShowModal()

            if res == wx.ID_OK:
                cmd_settings = cmd_dialog.get_settings()

            else:
                cmd_settings = None

            cmd_dialog.Destroy()

        else:
            cmd_settings = None

        return cmd_settings

    def _validate_cmd(self, cmd_settings):
        err_msg = ''

        if cmd_settings['item_type'] == 'sec_sample':
            # Do exposure verification and hplc param verification here
            cmd_settings['inst'] = '{}{}'.format(cmd_settings['inst'],
                cmd_settings['flow_path'])

            cmd_settings['data_dir'] = os.path.join(cmd_settings['data_dir'],
                cmd_settings['filename'])

            if cmd_settings['sp_method'] == 'None':
                cmd_settings['sp_method'] = ''

            if cmd_settings['frames_by_elut']:
                elution_time = float(cmd_settings['elution_vol'])/float(cmd_settings['flow_rate'])*60
                exp_time = elution_time*self.auto_panel.settings['exp_elut_scale']
                num_frames = int(round(exp_time/float(cmd_settings['exp_period'])+0.5))
                cmd_settings['num_frames'] = num_frames

            if cmd_settings['coflow_from_fr']:
                cmd_settings['coflow_fr'] = float(cmd_settings['flow_rate'])

            cmd_settings, exp_valid, exp_errors = self._validate_exp_params(
                cmd_settings)

            cmd_settings, coflow_valid, coflow_errors = self._validate_coflow_params(
                cmd_settings)

            cmd_settings, hplc_valid, hplc_errors = self._validate_hplc_injection_params(
                cmd_settings)

            if not exp_valid or not coflow_valid or not hplc_valid:
                err_msg = 'The following field(s) have invalid values:'

                if not exp_valid:
                    err_msg += '\nExposure settings:'
                    for err in exp_errors:
                        err_msg = err_msg + '\n- ' + err

                if not coflow_valid:
                    err_msg += '\nCoflow settings:'
                    for err in coflow_errors:
                        err_msg = err_msg + '\n- ' + err

                if not hplc_valid > 0:
                    err_msg += '\nInjection settings:'
                    for err in hplc_errors:
                        err_msg = err_msg + '\n- ' + err

        elif cmd_settings['item_type'] == 'equilibrate':
            # Do equilibration verification here

            cmd_settings['inst'] = '{}{}'.format(cmd_settings['inst'],
                cmd_settings['flow_path'])

            cmd_settings, hplc_valid, hplc_errors = self._validate_hplc_equil_params(
                cmd_settings)

            if not hplc_valid:
                err_msg = 'The following field(s) have invalid values:'

                for err in hplc_errors:
                    err_msg = err_msg + '\n- ' + err

        elif cmd_settings['item_type'] == 'switch_pumps':
            cmd_settings, hplc_valid, hplc_errors = self._validate_hplc_switch_params(
                cmd_settings)

            if not hplc_valid:
                err_msg = 'The following field(s) have invalid values:'

                for err in hplc_errors:
                    err_msg = err_msg + '\n- ' + err

        elif cmd_settings['item_type'] == 'exposure':
            # Do exposure verification here
            cmd_settings['data_dir'] = os.path.join(cmd_settings['data_dir'],
                cmd_settings['filename'])

            cmd_settings, exp_valid, exp_errors = self._validate_exp_params(
                cmd_settings, sec_saxs=False)

            if not exp_valid:
                err_msg = 'The following field(s) have invalid values:'

                for err in exp_errors:
                    err_msg = err_msg + '\n- ' + err

        if len(err_msg) > 0:
            valid = False
        else:
            valid = True

        return valid, err_msg

    def _validate_exp_params(self, cmd_settings, sec_saxs=True):
        exp_panel = wx.FindWindowByName('exposure')

        num_frames = cmd_settings['num_frames']
        exp_time = cmd_settings['exp_time']
        exp_period = cmd_settings['exp_period']
        data_dir = cmd_settings['data_dir']
        filename = cmd_settings['filename']
        wait_for_trig = cmd_settings['wait_for_trig']
        num_trig = cmd_settings['num_trig']
        struck_measurement_time = cmd_settings['struck_measurement_time']

        (num_frames, exp_time, exp_period, data_dir, filename,
            wait_for_trig, num_trig, local_data_dir, struck_num_meas, valid,
            errors) = exp_panel._validate_exp_values(
            num_frames, exp_time, exp_period, data_dir, filename,
            wait_for_trig, num_trig, struck_measurement_time, verbose=False,
            automator=True)

        if sec_saxs:
            if isinstance(exp_time, float) and exp_time < 0.125:
                errors.append('Exposure time with UV data collection must be >= 0.125 s')

            if (isinstance(exp_time, float) and isinstance(exp_period, float) and
                ((exp_period - exp_time) < 0.01)):
                errors.append(('Exposure period must be at least 0.01 s longer '
                    'than exposure time with UV data collection'))

        cmd_settings['num_frames'] = num_frames
        cmd_settings['exp_time'] = exp_time
        cmd_settings['exp_period'] = exp_period
        cmd_settings['data_dir'] = local_data_dir
        cmd_settings['filename'] = filename
        cmd_settings['wait_for_trig'] = wait_for_trig
        cmd_settings['num_trig'] = num_trig

        if len(errors) > 0:
            valid = False

        return cmd_settings, valid, errors

    def _validate_coflow_params(self, cmd_settings):
        errors = []

        column = cmd_settings['column']
        try:
            flow_rate = float(cmd_settings['flow_rate'])
        except ValueError:
            flow_rate = None

        if column is not None and flow_rate is not None:
            if '10/300' in column:
                flow_range = (0.4, 0.8)
            elif '5/150' in column:
                flow_range = (0.2, 0.5)
            elif 'Wyatt' in column:
                flow_range = (0.4, 0.8)
            else:
                flow_range = None

            if flow_range is not None:
                if flow_rate < flow_range[0] or flow_rate > flow_range[1]:
                    msg = ('Flow rate of {} is not in the usual '
                        'range of {} to {} for column {}'.format(flow_rate,
                        flow_range[0], flow_range[1], column))

                    errors.append(msg)

        if len(errors) > 0:
            valid = False
        else:
            valid = True

        return cmd_settings, valid, errors

    def _validate_hplc_injection_params(self, cmd_settings):
        hplc_panel = self.auto_panel.settings['instruments']['hplc']['hplc_panel']

        valid, errors = hplc_panel.validate_injection_params(cmd_settings)

        return cmd_settings, valid, errors

    def _validate_hplc_equil_params(self, cmd_settings):
        hplc_panel = self.auto_panel.settings['instruments']['hplc']['hplc_panel']

        valid, errors = hplc_panel.validate_equil_params(cmd_settings)

        return cmd_settings, valid, errors

    def _validate_hplc_switch_params(self, cmd_settings):
        hplc_panel = self.auto_panel.settings['instruments']['hplc']['hplc_panel']

        valid, errors = hplc_panel.validate_switch_params(cmd_settings)

        return cmd_settings, valid, errors

    def _add_item(self, item_info):

        command = self._on_add_item_callback(item_info)
        # auto_names = ['test']
        # auto_ids = [0]

        item_type = item_info['item_type']

        new_item = AutoListItem(self, item_type, command)

        self.add_items([new_item])


    def _on_remove_item(self, evt):
        sel_items = self.get_selected_items()

        item_list = [item.command for item in sel_items]

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
                prev_items = self.all_items[:top_idx]
                prev_states_done_or_abort = []
                for item in prev_items:
                    if item.status == 'done' or item.status == 'abort':
                        prev_states_done_or_abort.append(True)
                    else:
                        prev_states_done_or_abort.append(False)

                if all(prev_states_done_or_abort):
                    move_up = False

                item_states_done = [item.status == 'done' for item in sel_items]
                item_states_abort = [item.status == 'abort' for item in sel_items]
                item_states_wait = [item.status == 'wait' for item in sel_items]

                if (any(item_states_done) or any(item_states_abort) or
                    any(item_states_wait)):
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
                item_states_abort = [item.status == 'abort' for item in sel_items]
                item_states_wait = [item.status == 'wait' for item in sel_items]

                if (any(item_states_done) or any(item_states_abort) or
                    any(item_states_wait)):
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

            for name in item.command.auto_names:
                if name in switch_item.command.auto_names:
                    share_control = True
                    break

            if (any([status != 'queue' for status in switch_item.command.auto_id_status])
                and share_control):
                do_move = False
                break

        return do_move

    def _do_move_item(self, item, move):
        item_idx = self.get_item_index(item)

        if move == 'up':
            switch_item = self.all_items[item_idx-1]
            move_list = enumerate(item.command.auto_names)
        else:
            switch_item = self.all_items[item_idx+1]
            move_list = enumerate(item.command.auto_names)
            move_list = list(move_list)[::-1]

        for i, name in move_list:
            move_dist = self._get_shared_cmd_number(name, switch_item)

            if move_dist > 0:
                if move == 'down':
                    move_dist *= -1

                self._on_move_item_callback(item.command.auto_ids[i],
                    name, move_dist)

        self.move_item(item, move, False)

        self.resize_list()

    def _get_shared_cmd_number(self, cmd_name, item):
        return item.command.auto_names.count(cmd_name)

    def abort_item(self, aid):
        for item in self.all_items:
            if item.status != 'done' and item.status != 'abort':
                if aid in item.command.auto_ids:
                    item.abort()


class AutoListItem(utils.ListItem):
    def __init__(self, item_list, item_type, command, *args, **kwargs):
        self.item_type = item_type
        self.command = command
        self.item_info = command.cmd_info

        utils.ListItem.__init__(self, item_list, *args, **kwargs)

        self.command.add_status_change_callback(self._on_command_status_change)
        self.set_automator_status()

    def _create_layout(self):
        item_parent = self

        if self.item_type == 'sec_sample':
            item_label = 'SEC sample'
        elif self.item_type == 'switch_pumps':
            item_label = 'Switch pumps'
        elif self.item_type == 'exposure':
            item_label = 'Standalone Exposure'
        else:
            item_label = self.item_type.capitalize()

        if self.item_info['inst'].startswith('hplc'):
            if self.item_info['num_flow_paths'] == 1:
                inst_label = self.item_info['inst'].split('_')[0].upper()
            else:
                try:
                    inst_label = '{} {}'.format(self.item_info['inst'].split('_')[0].upper(),
                        self.item_info['inst'].split('_')[1].capitalize())
                except IndexError:
                    inst_label = '{}'.format(self.item_info['inst'].upper())

            item_label = '{}, {}'.format(item_label, inst_label)

        type_label = wx.StaticText(item_parent, label=item_label,
            size=self._FromDIP((210, -1)), style=wx.ST_NO_AUTORESIZE)

        fsize = self.GetFont().GetPointSize()
        font = wx.Font(fsize, wx.DEFAULT, wx.NORMAL, wx.BOLD)
        type_label.SetFont(font)

        self.text_list.append(type_label)

        self.status_ctrl = wx.StaticText(item_parent, label='',
            size=self._FromDIP((60, -1)), style=wx.ST_NO_AUTORESIZE)

        std_sizer = wx.BoxSizer(wx.HORIZONTAL)
        std_sizer.Add(type_label, flag=wx.RIGHT|wx.LEFT, border=self._FromDIP(5))
        std_sizer.Add(wx.StaticText(item_parent, label='Status:'), flag=wx.RIGHT,
            border=self._FromDIP(5))
        std_sizer.Add(self.status_ctrl, flag=wx.RIGHT|wx.LEFT,
            border=self._FromDIP(5))

        if self.item_type == 'sec_sample':

            name_label = wx.StaticText(item_parent, label='Name:')
            self.name_ctrl = wx.StaticText(item_parent, label='')

            desc_label = wx.StaticText(item_parent, label='Sample:')
            self.desc_ctrl = wx.StaticText(item_parent, label='')

            conc_label = wx.StaticText(item_parent, label='Conc. (mg/ml):')
            self.conc_ctrl = wx.StaticText(item_parent, label='')

            buffer_label = wx.StaticText(item_parent, label='Buffer:')
            self.buffer_ctrl = wx.StaticText(item_parent, label='')

            item_sizer = wx.BoxSizer(wx.HORIZONTAL)
            item_sizer.Add(name_label, flag=wx.RIGHT|wx.LEFT, border=self._FromDIP(2))
            item_sizer.Add(self.name_ctrl, flag=wx.RIGHT, border=self._FromDIP(10))
            item_sizer.Add(desc_label, flag=wx.RIGHT, border=self._FromDIP(2))
            item_sizer.Add(self.desc_ctrl, flag=wx.RIGHT, border=self._FromDIP(10))
            item_sizer.Add(conc_label, flag=wx.RIGHT, border=self._FromDIP(2))
            item_sizer.Add(self.conc_ctrl, flag=wx.RIGHT, border=self._FromDIP(10))
            item_sizer.Add(buffer_label, flag=wx.RIGHT, border=self._FromDIP(2))
            item_sizer.Add(self.buffer_ctrl, flag=wx.RIGHT, border=self._FromDIP(10))

            self.text_list.extend([desc_label, self.desc_ctrl,
                conc_label, self.conc_ctrl, buffer_label, self.buffer_ctrl])

        elif self.item_type == 'equilibrate':

            buffer_pos_label = wx.StaticText(item_parent, label='Buffer Pos.:')
            self.buffer_pos_ctrl = wx.StaticText(item_parent, label='')

            buffer_label = wx.StaticText(item_parent, label='Buffer:')
            self.buffer_ctrl = wx.StaticText(item_parent, label='')

            item_sizer = wx.BoxSizer(wx.HORIZONTAL)
            item_sizer.Add(buffer_pos_label, flag=wx.RIGHT|wx.LEFT,
                border=self._FromDIP(3))
            item_sizer.Add(self.buffer_pos_ctrl, flag=wx.RIGHT, border=self._FromDIP(10))
            item_sizer.Add(buffer_label, flag=wx.RIGHT|wx.LEFT,
                border=self._FromDIP(3))
            item_sizer.Add(self.buffer_ctrl, flag=wx.RIGHT, border=self._FromDIP(10))

            self.text_list.extend([buffer_label, self.buffer_ctrl])

        elif self.item_type == 'switch_pumps':
            item_sizer = wx.BoxSizer()

        elif self.item_type == 'exposure':
            name_label = wx.StaticText(item_parent, label='Filename:')
            self.name_ctrl = wx.StaticText(item_parent, label='')

            item_sizer = wx.BoxSizer(wx.HORIZONTAL)
            item_sizer.Add(name_label, flag=wx.RIGHT|wx.LEFT, border=self._FromDIP(5))
            item_sizer.Add(self.name_ctrl, flag=wx.RIGHT, border=self._FromDIP(5))



        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(std_sizer, flag=wx.TOP|wx.BOTTOM, border=self._FromDIP(5))
        top_sizer.Add(item_sizer, flag=wx.BOTTOM, border=self._FromDIP(5))


        # This should be moved into the list item?
        if self.item_type == 'sec_sample':
            name = self.item_info['filename']
            descrip = self.item_info['sample_name']
            conc = self.item_info['conc']
            buf = self.item_info['buf']
            self.set_name(name)
            self.set_description(descrip)
            self.set_concentration(conc)
            self.set_buffer(buf)

        elif self.item_type == 'equilibrate':
            buf = self.item_info['buf']
            buf_pos = self.item_info['buffer_position']
            self.set_buffer(buf)
            self.set_buffer_position(buf_pos)

        elif self.item_type == 'switch_pumps':
            pass

        elif self.item_type == 'exposure':
            name = self.item_info['filename']
            self.set_name(name)

        self.set_status_label('Queued')

        self.SetSizer(top_sizer)

    def set_description(self, descrip):
        self.desc_ctrl.SetLabel(descrip)

    def set_concentration(self, concentration):
        self.conc_ctrl.SetLabel('{}'.format(concentration))

    def set_buffer_position(self, pos):
        self.buffer_pos_ctrl.SetLabel(str(pos))
    def set_buffer(self, buffer_info):
        self.buffer_ctrl.SetLabel(buffer_info)

    def set_name(self, name):
        self.name_ctrl.SetLabel(name)

    def set_status_label(self, status):
        self.status_ctrl.SetLabel(status)

    def _on_command_status_change(self):
        wx.CallAfter(self.set_automator_status)

    def set_automator_status(self):
        self.status = self.command.get_command_status()

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

    def abort(self):
        self.command.abort()
        self.status_ctrl.SetLabel('Aborted')
        self.status = 'abort'

class AutoCmdDialog(wx.Dialog):
    """
    Allows addition/editing of the buffer info in the buffer list
    """
    def __init__(self, parent, default_settings, *args, **kwargs):
        wx.Dialog.__init__(self, parent, *args,
            style=wx.RESIZE_BORDER|wx.CAPTION|wx.CLOSE_BOX, **kwargs)

        self._default_settings = default_settings
        self.ctrl_ids = {}

        for key in default_settings.keys():
            self.ctrl_ids[key] = wx.NewIdRef()

        self._create_layout()
        self._init_settings()

        utils.set_best_size(self)
        self.CenterOnParent()

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _create_layout(self):
        pass

    def _init_settings(self):
        for key, c_id in self.ctrl_ids.items():
            default_val = self._default_settings[key]
            ctrl = self.FindWindowById(c_id)

            if ctrl is not None:
                if isinstance(ctrl, wx.Choice):
                    ctrl.SetStringSelection(str(default_val))
                else:
                    try:
                        ctrl.SetValue(str(default_val))
                    except TypeError:
                        ctrl.SetValue(default_val)

    def get_settings(self):
        cmd_settings = {}

        for key, c_id in self.ctrl_ids.items():
            ctrl = self.FindWindowById(c_id)

            if ctrl is not None:
                if isinstance(ctrl, wx.Choice):
                    cmd_settings[key] =ctrl.GetStringSelection()
                else:
                    cmd_settings[key] =ctrl.GetValue()
            else:
                cmd_settings[key] = self._default_settings[key]

        return cmd_settings

    def on_collapse(self, event):
        self.Layout()
        self.Refresh()
        self.SendSizeEvent()

class SecSampleCmdDialog(AutoCmdDialog):
    """
    Allows addition/editing of the buffer info in the buffer list
    """
    def __init__(self, parent, default_settings, acq_methods, sample_methods, *args, **kwargs):
        self.acq_methods = acq_methods
        self.sample_methods = sample_methods
        self.sample_methods.insert(0, 'None')

        AutoCmdDialog.__init__(self, parent, default_settings, *args, **kwargs)

    def _create_layout(self):
        parent = self

        num_flow_paths = self._default_settings['num_flow_paths']
        self.acq_methods
        self.sample_methods

        cmd_sizer = make_sec_saxs_info_panel(self, self, self.ctrl_ids, 'horiz')

        button_sizer = self.CreateButtonSizer(wx.OK | wx.CANCEL)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(cmd_sizer, proportion=1, flag=wx.EXPAND|wx.ALL,
            border=self._FromDIP(5))
        top_sizer.Add(button_sizer ,flag=wx.BOTTOM|wx.RIGHT|wx.LEFT|wx.ALIGN_RIGHT,
            border=self._FromDIP(5))

        self.SetSizer(top_sizer)

class EquilibrateDialog(AutoCmdDialog):
    """
    Allows addition/editing of the buffer info in the buffer list
    """
    def __init__(self, default_settings, *args, **kwargs):
        AutoCmdDialog.__init__(self, default_settings, *args, **kwargs)

    def _create_layout(self):
        parent = self
        top_level = self

        ################ HPLC #################
        fp_choices = ['{}'.format(i+1) for i in range(int(self._default_settings['num_flow_paths']))]
        buffer_choices = ['{}'.format(i) for i in range(1,11)]

        equil_settings = {
            'equil_rate'    : ['Equilibration rate [mL/min]:', self.ctrl_ids['equil_rate'], 'float'],
            'equil_vol'     : ['Equilibration volume [mL]:', self.ctrl_ids['equil_vol'], 'float'],
            'purge'         : ['Run purge', self.ctrl_ids['purge'], 'bool'],
            'flow_path'     : ['Flow path:', self.ctrl_ids['flow_path'], 'choice', fp_choices],
            'buffer_position': ['Buffer position:', self.ctrl_ids['buffer_position'],
                                'choice', buffer_choices],
            }

        equil_adv_settings = {
            'equil_accel'   : ['Equilibration acceleration [mL/min^2]:',
                                self.ctrl_ids['equil_accel'], 'float'],
            'purge_volume'  : ['Purge volume [mL]:', self.ctrl_ids['purge_volume'], 'float'],
            'purge_rate'    : ['Purge rate [mL/min]:', self.ctrl_ids['purge_rate'], 'float'],
            'purge_accel'   : ['Purge acceleration [mL/min^2]:',
                                self.ctrl_ids['purge_accel'], 'float'],
            'stop_after_equil': ['Stop flow after equilibration',
                                self.ctrl_ids['stop_after_equil'], 'bool'],
        }

        equil_box = wx.StaticBox(parent, label='HPLC Settings')

        equil_adv_pane = wx.CollapsiblePane(equil_box, label="Advanced Settings")
        equil_adv_pane.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, self.on_collapse)
        equil_adv_win = equil_adv_pane.GetPane()

        equil_sizer1 = create_info_sizer(equil_settings, top_level, equil_box)
        equil_sizer2 = create_info_sizer(equil_adv_settings, top_level, equil_adv_win)

        equil_adv_win.SetSizer(equil_sizer2)
        equil_adv_pane.Collapse()

        equil_sizer = wx.StaticBoxSizer(equil_box, wx.VERTICAL)
        equil_sizer.Add(equil_sizer1, flag=wx.EXPAND|wx.ALL, border=self._FromDIP(5))
        equil_sizer.Add(equil_adv_pane, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))


        ################ Coflow #################
        coflow_buffer_choices = ['{}'.format(i) for i in range(1,11)]
        coflow_settings = {
            'coflow_equil'  : ['Equilibrate coflow', self.ctrl_ids['coflow_equil'], 'bool'],
            'coflow_buf_pos': ['Buffer position:', self.ctrl_ids['coflow_buf_pos'],
                                'choice', coflow_buffer_choices],
            }

        coflow_adv_settings = {
            'coflow_restart': ['Restart coflow after equilibration',
                                self.ctrl_ids['coflow_restart'], 'bool'],
            'coflow_rate'   : ['Restart flow rate [mL/min]:',
                                self.ctrl_ids['coflow_rate'], 'float'],
            }

        coflow_box = wx.StaticBox(parent, label='Coflow Settings')

        coflow_adv_pane = wx.CollapsiblePane(coflow_box, label="Advanced Settings")
        coflow_adv_pane.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, self.on_collapse)
        coflow_adv_win = coflow_adv_pane.GetPane()

        coflow_sizer1 = create_info_sizer(coflow_settings, top_level, coflow_box)
        coflow_sizer2 = create_info_sizer(coflow_adv_settings, top_level, coflow_adv_win)

        coflow_adv_win.SetSizer(coflow_sizer2)
        coflow_adv_pane.Collapse()

        coflow_sizer = wx.StaticBoxSizer(coflow_box, wx.VERTICAL)
        coflow_sizer.Add(coflow_sizer1, flag=wx.ALL, border=self._FromDIP(5))
        coflow_sizer.Add(coflow_adv_pane, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))

        button_sizer = self.CreateButtonSizer(wx.OK | wx.CANCEL)

        cmd_sizer=wx.BoxSizer(wx.HORIZONTAL)
        cmd_sizer.Add(equil_sizer, flag=wx.TOP|wx.RIGHT|wx.BOTTOM|wx.EXPAND,
            border=self._FromDIP(5))
        cmd_sizer.Add(coflow_sizer, flag=wx.TOP|wx.RIGHT|wx.BOTTOM|wx.EXPAND,
            border=self._FromDIP(5))

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(cmd_sizer, proportion=1, flag=wx.EXPAND|wx.ALL,
            border=self._FromDIP(5))
        top_sizer.Add(button_sizer ,flag=wx.BOTTOM|wx.RIGHT|wx.LEFT|wx.ALIGN_RIGHT,
            border=self._FromDIP(5))

        self.SetSizer(top_sizer)

class SwitchDialog(AutoCmdDialog):
    """
    Allows addition/editing of the buffer info in the buffer list
    """
    def __init__(self, default_settings, *args, **kwargs):
        AutoCmdDialog.__init__(self, default_settings, *args, **kwargs)

    def _create_layout(self):
        parent = self
        top_level = self

        ################ HPLC #################
        fp_choices = ['1', '2']

        switch_settings = {
            'flow_path'     : ['Switch to flow path:', self.ctrl_ids['flow_path'],
                                'choice', fp_choices],
        }

        switch_adv_settings = {
            'restore_flow_after_switch' : ['Restore flow to current rate after switching',
                                    self.ctrl_ids['restore_flow_after_switch'], 'bool'],
            'stop_flow1'     : ['Ramp pump 1 flow to 0 before switching',
                                    self.ctrl_ids['stop_flow1'], 'bool'],
            'stop_flow2'     : ['Ramp pump 2 flow to 0 before switching',
                                self.ctrl_ids['stop_flow2'], 'bool'],
            'purge_active'   : ['Purge active flow path after switching',
                                self.ctrl_ids['purge_active'], 'bool'],
            'purge_rate'    : ['Purge rate [mL/min]:', self.ctrl_ids['purge_rate'], 'float'],
            'purge_volume'  : ['Purge volume [mL]:', self.ctrl_ids['purge_volume'], 'float'],
            'purge_accel'   : ['Purge acceleration [mL/min^2]:',
                                self.ctrl_ids['purge_accel'], 'float'],
        }

        switch_box = wx.StaticBox(parent, label='HPLC Settings')

        switch_adv_pane = wx.CollapsiblePane(switch_box, label="Advanced Settings")
        switch_adv_pane.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, self.on_collapse)
        switch_adv_win = switch_adv_pane.GetPane()

        switch_sizer1 = create_info_sizer(switch_settings, top_level, switch_box)
        switch_sizer2 = create_info_sizer(switch_adv_settings, top_level, switch_adv_win)

        switch_adv_win.SetSizer(switch_sizer2)
        switch_adv_pane.Collapse()

        switch_sizer = wx.StaticBoxSizer(switch_box, wx.VERTICAL)
        switch_sizer.Add(switch_sizer1, flag=wx.EXPAND|wx.ALL, border=self._FromDIP(5))
        switch_sizer.Add(switch_adv_pane, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))


        ################ Coflow #################
        coflow_buffer_choices = ['{}'.format(i) for i in range(1,11)]
        coflow_settings = {
            'coflow_buf_pos': ['Buffer position:', self.ctrl_ids['coflow_buf_pos'],
                                'choice', coflow_buffer_choices],
            'coflow_rate'   : ['Restart flow rate [mL/min]:',
                                self.ctrl_ids['coflow_rate'], 'float'],
            }

        coflow_adv_settings = {
            'coflow_equil'  : ['Equilibrate coflow', self.ctrl_ids['coflow_equil'], 'bool'],
            'coflow_restart': ['Restart coflow after equilibration',
                                self.ctrl_ids['coflow_restart'], 'bool'],
            }

        coflow_box = wx.StaticBox(parent, label='Coflow Settings')

        coflow_adv_pane = wx.CollapsiblePane(coflow_box, label="Advanced Settings")
        coflow_adv_pane.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, self.on_collapse)
        coflow_adv_win = coflow_adv_pane.GetPane()

        coflow_sizer1 = create_info_sizer(coflow_settings, top_level, coflow_box)
        coflow_sizer2 = create_info_sizer(coflow_adv_settings, top_level, coflow_adv_win)

        coflow_adv_win.SetSizer(coflow_sizer2)
        coflow_adv_pane.Collapse()

        coflow_sizer = wx.StaticBoxSizer(coflow_box, wx.VERTICAL)
        coflow_sizer.Add(coflow_sizer1, flag=wx.EXPAND|wx.ALL, border=self._FromDIP(5))
        coflow_sizer.Add(coflow_adv_pane, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))


        button_sizer = self.CreateButtonSizer(wx.OK | wx.CANCEL)

        cmd_sizer=wx.BoxSizer(wx.HORIZONTAL)
        cmd_sizer.Add(switch_sizer, flag=wx.TOP|wx.RIGHT|wx.BOTTOM|wx.EXPAND,
            border=self._FromDIP(5))
        cmd_sizer.Add(coflow_sizer, flag=wx.TOP|wx.RIGHT|wx.BOTTOM|wx.EXPAND,
            border=self._FromDIP(5))

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(cmd_sizer, proportion=1, flag=wx.EXPAND|wx.ALL,
            border=self._FromDIP(5))
        top_sizer.Add(button_sizer ,flag=wx.BOTTOM|wx.RIGHT|wx.LEFT|wx.ALIGN_RIGHT,
            border=self._FromDIP(5))

        self.SetSizer(top_sizer)

class ExposureCmdDialog(AutoCmdDialog):
    """
    Allows addition/editing of the buffer info in the buffer list
    """
    def __init__(self, default_settings, *args, **kwargs):
        AutoCmdDialog.__init__(self, default_settings, *args, **kwargs)

    def _create_layout(self):
        parent = self
        top_level = self

        cmd_sizer = make_standalone_exp_panel(top_level, parent, self.ctrl_ids,
            'horiz')

        button_sizer = self.CreateButtonSizer(wx.OK | wx.CANCEL)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(cmd_sizer, proportion=1, flag=wx.EXPAND|wx.ALL,
            border=self._FromDIP(5))
        top_sizer.Add(button_sizer ,flag=wx.BOTTOM|wx.RIGHT|wx.LEFT|wx.ALIGN_RIGHT,
            border=self._FromDIP(5))

        self.SetSizer(top_sizer)

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



#######################################################
default_automator_settings = {
        'automator_thread'  : None,
        'instruments'       : {},
        'exp_elut_scale'    : 0.95,
        'hplc_inst'         : '',
        }



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

    #  # SEC-MALS HPLC-1
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


    # # SEC-SAXS 2 pump
    # hplc_args = {
    #     'name'  : 'SEC-SAXS',
    #     'args'  : ['AgilentHPLC', 'net.pipe://localhost/Agilent/OpenLAB/'],
    #     'kwargs': {'instrument_name': 'SEC-SAXS', 'project_name': 'Demo',
    #                 'get_inst_method_on_start': True}
    #     }

    # selector_valve_args = {
    #     'name'  : 'Selector',
    #     'args'  : ['Cheminert', 'COM5'],
    #     'kwargs': {'positions' : 2}
    #     }

    # outlet_valve_args = {
    #     'name'  : 'Outlet',
    #     'args'  : ['Cheminert', 'COM8'],
    #     'kwargs': {'positions' : 2}
    #     }

    # purge1_valve_args = {
    #     'name'  : 'Purge 1',
    #     'args'  : ['Cheminert', 'COM9'],
    #     'kwargs': {'positions' : 4}
    #     }

    # purge2_valve_args = {
    #     'name'  : 'Purge 2',
    #     'args'  : ['Cheminert', 'COM6'],
    #     'kwargs': {'positions' : 4}
    #     }

    # buffer1_valve_args = {
    #     'name'  : 'Buffer 1',
    #     'args'  : ['Cheminert', 'COM3'],
    #     'kwargs': {'positions' : 10}
    #     }

    # buffer2_valve_args = {
    #     'name'  : 'Buffer 2',
    #     'args'  : ['Cheminert', 'COM4'],
    #     'kwargs': {'positions' : 10}
    #     }

    # # 2 pump HPLC for SEC-SAXS
    # setup_devices = [
    #     {'name': 'SEC-SAXS', 'args': ['AgilentHPLC2Pumps', None],
    #         'kwargs': {'hplc_args' : hplc_args,
    #         'selector_valve_args' : selector_valve_args,
    #         'outlet_valve_args' : outlet_valve_args,
    #         'purge1_valve_args' : purge1_valve_args,
    #         'purge2_valve_args' : purge2_valve_args,
    #         'buffer1_valve_args' : buffer1_valve_args,
    #         'buffer2_valve_args' : buffer2_valve_args,
    #         'pump1_id' : 'quat. pump 1#1c#1',
    #         'pump2_id' : 'quat. pump 2#1c#2'},
    #         }
    #     ]

    # # Local
    # com_thread = biohplccon.HPLCCommThread('HPLCComm')
    # com_thread.start()

    # Remote
    hplc_settings = biohplccon.default_hplc_2pump_settings
    hplc_settings['com_thread'] = None
    hplc_settings['remote'] = True
    hplc_settings['remote_device'] = 'hplc'
    hplc_settings['remote_ip'] = '164.54.204.113'
    hplc_settings['remote_port'] = '5556'
    hplc_settings['device_data'] = hplc_settings['device_init'][0]

    # #Settings
    # coflow_settings = {
    #     'show_advanced_options'     : False,
    #     'device_communication'      : 'remote',
    #     'remote_pump_ip'            : '164.54.204.53',
    #     'remote_pump_port'          : '5556',
    #     'remote_fm_ip'              : '164.54.204.53',
    #     'remote_fm_port'            : '5557',
    #     'remote_overflow_ip'        : '164.54.204.75',
    #     'remote_valve_ip'           : '164.54.204.53',
    #     'remote_valve_port'         : '5558',
    #     'flow_units'                : 'mL/min',
    #     'sheath_pump'               : {'name': 'sheath', 'args': ['VICI M50', 'COM3'],
    #                                     'kwargs': {'flow_cal': '627.72',
    #                                     'backlash_cal': '9.814'},
    #                                     'ctrl_args': {'flow_rate': 1}},
    #     # 'outlet_pump'               : {'name': 'outlet', 'args': ['VICI M50', 'COM4'],
    #     #                                 'kwargs': {'flow_cal': '628.68',
    #     #                                 'backlash_cal': '9.962'},
    #     #                                 'ctrl_args': {'flow_rate': 1}},
    #     'outlet_pump'               : {'name': 'outlet', 'args': ['OB1 Pump', 'COM8'],
    #                                     'kwargs': {'ob1_device_name': 'Outlet OB1', 'channel': 1,
    #                                     'min_pressure': -1000, 'max_pressure': 1000, 'P': 5, 'I': 0.00015,
    #                                     'D': 0, 'bfs_instr_ID': None, 'comm_lock': None,
    #                                     'calib_path': './resources/ob1_calib.txt'},
    #                                     'ctrl_args': {}},
    #     'sheath_fm'                 : {'name': 'sheath', 'args': ['BFS', 'COM6'],
    #                                     'kwargs':{}},
    #     'outlet_fm'                 : {'name': 'outlet', 'args': ['BFS', 'COM5'],
    #                                     'kwargs':{}},
    #     'sheath_valve'              : {'name': 'Coflow Sheath',
    #                                     'args':['Cheminert', 'COM4'],
    #                                     'kwargs': {'positions' : 10}},
    #     # 'sheath_pump'               : {'name': 'sheath', 'args': ['Soft', None], # Simulated devices for testing
    #     #                                 'kwargs': {}},
    #     # 'outlet_pump'               : {'name': 'outlet', 'args': ['Soft', None],
    #     #                                 'kwargs': {}},
    #     # 'sheath_fm'                 : {'name': 'sheath', 'args': ['Soft', None],
    #     #                                 'kwargs':{}},
    #     # 'outlet_fm'                 : {'name': 'outlet', 'args': ['Soft', None],
    #     #                                 'kwargs':{}},
    #     # 'sheath_valve'              : {'name': 'Coflow Sheath',
    #     #                                 'args': ['Soft', None],
    #     #                                 'kwargs': {'positions' : 10}},
    #     'sheath_ratio'              : 0.3,
    #     'sheath_excess'             : 1.5,
    #     'sheath_warning_threshold_low'  : 0.8,
    #     'sheath_warning_threshold_high' : 1.2,
    #     'outlet_warning_threshold_low'  : 0.8,
    #     'outlet_warning_threshold_high' : 1.2,
    #     # 'outlet_warning_threshold_low'  : 0.98,
    #     # 'outlet_warning_threshold_high' : 1.02,
    #     'sheath_fr_mult'            : 1,
    #     'outlet_fr_mult'            : 1,
    #     # 'outlet_fr_mult'            : -1,
    #     'settling_time'             : 5000, #in ms
    #     # 'settling_time'             : 120000, #in ms
    #     'lc_flow_rate'              : '0.1',
    #     'show_sheath_warning'       : True,
    #     'show_outlet_warning'       : True,
    #     'use_overflow_control'      : True,
    #     'buffer_change_fr'          : 0.1, #in ml/min
    #     'buffer_change_vol'         : 0.1, #in ml
    #     'air_density_thresh'        : 700, #g/L
    #     'sheath_valve_water_pos'    : 10,
    #     'sheath_valve_hellmanex_pos': 8,
    #     'sheath_valve_ethanol_pos'  : 9,
    #     }

    # coflow_settings['components'] = ['coflow']

    app = wx.App()
    # logger.debug('Setting up wx app')

    hplc_frame = biohplccon.HPLCFrame('HPLCFrame', hplc_settings, parent=None,
        title='HPLC Control')
    hplc_frame.Show()

    # coflow_frame = coflowcon.CoflowFrame(coflow_settings, True, parent=None, title='Coflow Control')
    # coflow_frame.Show()


    hplc_panel = hplc_frame.devices[0]
    hplc_automator_callback = hplc_frame.devices[0].automator_callback
    # coflow_automator_callback = coflow_frame.coflow_panel.automator_callback

    automator_settings = default_automator_settings
    automator_settings['automator_thread'] = automator
    automator_settings['hplc_inst'] = 'hplc'
    automator_settings['instruments'] = {
        'hplc'    : {'num_paths': 2,
                    'automator_callback': hplc_automator_callback,
                    'hplc_panel'    : hplc_panel,},
        # 'coflow'    : {'automator_callback': coflow_automator_callback},
        # 'exp'       : {'automator_callback': test_cmd_func}
        }


    # app = wx.App()
    logger.debug('Setting up wx app')
    frame = AutoFrame('AutoFrame', automator_settings, parent=None,
        title='Automator Control')
    frame.Show()

    app.MainLoop()

    if automator is not None:
        automator.stop()
        automator.join()

    # if com_thread is not None:
    #     com_thread.stop()
    #     com_thread.join()

