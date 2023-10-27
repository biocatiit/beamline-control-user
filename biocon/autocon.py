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

            with controls['cmd_lock']:
                cmd_func = controls['cmd_func']


                cmd_name = 'status'
                cmd_args = []
                cmd_kwargs = {'inst_name': name}

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

                logger.info(('Automator: {} running cmd {} with args {} and kwargs'
                    ' {}').format(name, cmd_name, cmd_args, cmd_kwargs))

                if not cmd_name.startswith('wait'):
                    state = cmd_func(cmd_name, cmd_args, cmd_kwargs)

                    if state is not None:
                        controls['status']['state'] = state

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
        self._sample_wait_id = 0
        self._switch_wait_id = 0

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
            self._on_remove_item_callback, self, self)

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

            sample_wait_cmd = 'wait_sample_{}'.format(self._sample_wait_id)
            cmd_id1 = self.automator.add_cmd('exp', sample_wait_cmd, [], {'condition': 'status',
                'inst_conds': [[hplc_inst, [sample_wait_cmd,]], ['exp', [sample_wait_cmd,]]]})
            cmd_id2 = self.automator.add_cmd('exp', 'expose', [], item_info)

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

            cmd_id3 = self.automator.add_cmd(hplc_inst, sample_wait_cmd, [],
                {'condition': 'status', 'inst_conds': [[hplc_inst,
                [sample_wait_cmd,]], ['exp', ['idle',]]]})
            cmd_id4 = self.automator.add_cmd(hplc_inst, 'inject', [], inj_settings)

            self._sample_wait_id += 1

            auto_names = ['exp', 'exp', hplc_inst, hplc_inst]
            auto_ids = [cmd_id1, cmd_id2, cmd_id3, cmd_id4]

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

            # cmd_id1 = self.automator.add_cmd(hplc_inst, 'wait', [],
            #     {'condition' : 'status', 'inst_conds': [[hplc_inst, 'idle'],]})
            cmd_id2 = self.automator.add_cmd(hplc_inst, 'equilibrate', [], equil_settings)

            # auto_names = [hplc_inst, hplc_inst]
            # auto_ids = [cmd_id1, cmd_id2]
            auto_names = [hplc_inst,]
            auto_ids = [cmd_id2,]

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

            inst_settings = self.settings['instruments'][hplc_inst]

            num_paths = inst_settings['num_flow_paths']

            switch_wait_cmd = 'wait_switch_{}'.format(self._switch_wait_id)

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

            self._switch_wait_id += 1

        return auto_names, auto_ids


    def _on_remove_item_callback(self, cmd_list):
        self.automator.set_automator_state('pause')

        for cmds in cmd_list:
            for i in range(len(cmds[0])):
                cmd_name =  cmd[0][i]
                cmd_id = cmd[1][i]
                self.automator.remove_cmd(cmd_name, cmd_id)

        self.automator.set_automator_state('run')


class AutoList(utils.ItemList):
    def __init__(self, on_add_item_callback, on_remove_item_callback,
        auto_panel, *args, **kwargs):
        utils.ItemList.__init__(self, *args)

        self.auto_panel = auto_panel

        self._on_add_item_callback = on_add_item_callback
        self._on_remove_item_callback = on_remove_item_callback

    def _create_buttons(self):
        button_parent = self

        add_item_btn = wx.Button(button_parent, label='Add Action')
        add_item_btn.Bind(wx.EVT_BUTTON, self._on_add_item)

        remove_item_btn = wx.Button(button_parent, label='Remove Action')
        remove_item_btn.Bind(wx.EVT_BUTTON, self._on_remove_item)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(add_item_btn, border=self._FromDIP(5), flag=wx.LEFT)
        button_sizer.Add(remove_item_btn, border=self._FromDIP(5), flag=wx.LEFT)

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

        self.add_items([new_item])


    def _on_remove_item(self, evt):
        sel_items = self.get_selected_items()

        item_list = [[item.automator_names, item.automator_ids] for item in sel_items]

        # self._on_remove_item_callback(item_list)

        self.remove_selected_items()



class AutoListItem(utils.ListItem):
    def __init__(self, item_list, item_type, auto_names, auto_ids, item_info,
        *args, **kwargs):
        self.item_type = item_type
        self.item_info = item_info

        utils.ListItem.__init__(self, item_list, *args, **kwargs)

        self.automator_names = auto_names
        self.automator_ids = auto_ids

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

        if self.item_type == 'sec_sample':

            name_label = wx.StaticText(item_parent, label='Name:')
            self.name_ctrl = wx.StaticText(item_parent, label='')

            desc_label = wx.StaticText(item_parent, label='Descripton:')
            self.desc_ctrl = wx.StaticText(item_parent, label='')

            conc_label = wx.StaticText(item_parent, label='Conc. (mg/ml):')
            self.conc_ctrl = wx.StaticText(item_parent, label='')

            buffer_label = wx.StaticText(item_parent, label='Buffer:')
            self.buffer_ctrl = wx.StaticText(item_parent, label='')

            top_sizer = wx.BoxSizer(wx.HORIZONTAL)
            top_sizer.Add(type_label, flag=wx.RIGHT, border=self._FromDIP(5))
            top_sizer.Add(name_label, flag=wx.RIGHT, border=self._FromDIP(3))
            top_sizer.Add(self.name_ctrl, flag=wx.RIGHT, border=self._FromDIP(3))
            top_sizer.Add(desc_label, flag=wx.RIGHT, border=self._FromDIP(3))
            top_sizer.Add(self.desc_ctrl, flag=wx.RIGHT, border=self._FromDIP(3))
            top_sizer.Add(conc_label, flag=wx.RIGHT, border=self._FromDIP(3))
            top_sizer.Add(self.conc_ctrl, flag=wx.RIGHT, border=self._FromDIP(3))
            top_sizer.Add(buffer_label, flag=wx.RIGHT, border=self._FromDIP(3))
            top_sizer.Add(self.buffer_ctrl, flag=wx.RIGHT, border=self._FromDIP(3))

            self.text_list.extend([desc_label, self.desc_ctrl,
                conc_label, self.conc_ctrl, buffer_label, self.buffer_ctrl])

        if self.item_type == 'equilibrate':

            buffer_label = wx.StaticText(item_parent, label='Buffer:')
            self.buffer_ctrl = wx.StaticText(item_parent, label='')

            top_sizer = wx.BoxSizer(wx.HORIZONTAL)
            top_sizer.Add(type_label, flag=wx.RIGHT, border=self._FromDIP(5))
            top_sizer.Add(buffer_label, flag=wx.RIGHT, border=self._FromDIP(3))
            top_sizer.Add(self.buffer_ctrl, flag=wx.RIGHT, border=self._FromDIP(3))

            self.text_list.extend([buffer_label, self.buffer_ctrl])

        self.SetSizer(top_sizer)


    def set_description(self, descrip):
        self.desc_ctrl.SetLabel(descrip)

    def set_concentration(self, concentration):
        self.conc_ctrl.SetLabel('{}'.format(concentration))

    def set_buffer(self, buffer_info):
        self.buffer_ctrl.SetLabel(buffer_info)

    def set_name(self, name):
        self.name_ctrl.SetLabel(name)


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

    automator_settings = {
        'automator_thread'  : automator,
        'instruments'       : {'hplc1' : {'num_paths': 1,
                                'automator_callback': hplc_automator_callback}}
        }


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

