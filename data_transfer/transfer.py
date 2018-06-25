# coding: utf-8
#
#    Project: BioCAT user data transfer
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

import os
import time
import subprocess
import threading

import wx


class TransferFrame(wx.Frame):
    def __init__(self, *args, **kwargs):
        super(TransferFrame, self).__init__(*args, **kwargs)

        self.source = ''
        self.dest = ''
        self.interval = ''

        self.backup_in_progress = False

        self.auto_timer = wx.Timer()
        self.auto_timer.Bind(wx.EVT_TIMER, self._on_auto_timer)

        self.backup_timer = wx.Timer()
        self.backup_timer.Bind(wx.EVT_TIMER, self._on_backup_timer)

        self.backup_thread = None

        self.abort_event = threading.Event()

        self._create_layout()

        self.Layout()
        self.Fit()
        self.Raise()

    def _create_layout(self):


        self.source_dir = wx.TextCtrl(self, value='', size=(400, -1), style=wx.TE_READONLY)
        source_browse = wx.Button(self, label='Browse', name='source_browse')
        source_browse.Bind(wx.EVT_BUTTON, self._on_browse)

        self.dest_dir = wx.TextCtrl(self, value='', size=(400, -1), style=wx.TE_READONLY)
        dest_browse = wx.Button(self, label='Browse', name='dest_browse')
        dest_browse.Bind(wx.EVT_BUTTON, self._on_browse)

        dir_sizer = wx.FlexGridSizer(rows=2, cols=3, hgap=5, vgap=10)
        dir_sizer.Add(wx.StaticText(self, label='Source:'))
        dir_sizer.Add(self.source_dir)
        dir_sizer.Add(source_browse)
        dir_sizer.Add(wx.StaticText(self, label='Destination:'))
        dir_sizer.Add(self.dest_dir)
        dir_sizer.Add(dest_browse)


        self.interval_ctrl = wx.TextCtrl(self, value='5', size=(60, -1))

        self.start_auto_btn = wx.Button(self, label='Start Automatic Backup')
        self.start_auto_btn.Bind(wx.EVT_BUTTON, self._on_start_auto)
        self.stop_auto_btn = wx.Button(self, label = 'Stop Automatic Backup')
        self.stop_auto_btn.Bind(wx.EVT_BUTTON, self._on_stop_auto)
        self.stop_auto_btn.Disable()

        self.start_manual_btn = wx.Button(self, label='Start Manual Backup')
        self.start_manual_btn.Bind(wx.EVT_BUTTON, self._on_start_manual)
        self.stop_manual_btn = wx.Button(self, label='Stop Backup Immediately')
        self.stop_manual_btn.Bind(wx.EVT_BUTTON, self._on_stop_manual)
        self.stop_manual_btn.Disable()


        timer_sizer = wx.BoxSizer(wx.HORIZONTAL)
        timer_sizer.Add(wx.StaticText(self, label='Backup Interval (min.):'))
        timer_sizer.Add(self.interval_ctrl, border=5, flag=wx.LEFT)

        auto_sizer = wx.BoxSizer(wx.HORIZONTAL)
        auto_sizer.Add(self.start_auto_btn)
        auto_sizer.Add(self.stop_auto_btn, border=5, flag=wx.LEFT)

        manual_sizer = wx.BoxSizer(wx.HORIZONTAL)
        manual_sizer.Add(self.start_manual_btn)
        manual_sizer.Add(self.stop_manual_btn, border=5, flag=wx.LEFT)

        ctrl_sizer = wx.BoxSizer(wx.VERTICAL)
        ctrl_sizer.Add(timer_sizer)
        ctrl_sizer.Add(auto_sizer, border=5, flag=wx.TOP|wx.ALIGN_CENTER_HORIZONTAL)
        ctrl_sizer.Add(manual_sizer, border=5, flag=wx.TOP|wx.ALIGN_CENTER_HORIZONTAL)

        self.status = wx.StaticText(self, label='Ready', size=(390, -1))
        font = wx.Font(22, wx.DEFAULT, wx.NORMAL, wx.BOLD)
        self.status.SetFont(font)

        status_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Status'), wx.VERTICAL)
        status_sizer.Add(self.status, 1, border=5, flag=wx.ALL|wx.EXPAND)


        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(dir_sizer, border=10, flag=wx.ALL)
        top_sizer.Add(ctrl_sizer, border=10, flag=wx.ALL|wx.EXPAND)
        top_sizer.Add(status_sizer, border=10, flag=wx.ALL|wx.ALIGN_CENTER_HORIZONTAL)

        self.SetSizer(top_sizer)

    def _on_browse(self, event):
        name = event.GetEventObject().GetName()

        if name == 'source_browse':
            msg="Select the source directory"
            style = wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST
            default_path = '/nas_data/Pilatus1M'
        else:
            msg="Select the destination directory"
            style = wx.DD_DEFAULT_STYLE
            default_path = ''

        dlg = wx.DirDialog(self, msg, defaultPath=default_path, style=style)

        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
        else:
            return

        dlg.Destroy()

        if name == 'source_browse':
            self.source_dir.SetValue(path)
        else:
            self.dest_dir.SetValue(path)

        return

    def _on_start_auto(self, event):
        self.source = self.source_dir.GetValue()
        self.dest = self.dest_dir.GetValue()

        try:
            self.interval = float(self.interval_ctrl.GetValue())
        except ValueError:
            msg = 'You must have a number for the backup interval.'
            wx.MessageBox(msg, 'Select backup interval')
            return

        if self.source == '':
            msg = 'You must pick a source directory.'
            wx.MessageBox(msg, 'Select source directory')
            return

        if self.dest == '':
            msg = 'You must pick a destination directory.'
            wx.MessageBox(msg, 'Select destination directory')
            return

        self.auto_timer.Start(1000)
        self.start_time = time.time()

        self.start_manual_btn.Disable()
        self.start_auto_btn.Disable()
        self.stop_manual_btn.Enable()
        self.stop_auto_btn.Enable()

        self._backup()

    def _on_stop_auto(self, event):
        self.auto_timer.Stop()

        if not self.backup_in_progress:
            self.start_manual_btn.Enable()
            self.start_auto_btn.Enable()
            self.stop_manual_btn.Disable()
            self.stop_auto_btn.Disable()
            self.status.SetLabel('Ready')

    def _on_start_manual(self, event):

        self.source = self.source_dir.GetValue()
        self.dest = self.dest_dir.GetValue()
        self.interval = float(self.interval_ctrl.GetValue())

        if self.source == '':
            msg = 'You must pick a source directory.'
            wx.MessageBox(msg, 'Select source directory')
            return

        if self.dest == '':
            msg = 'You must pick a destination directory.'
            wx.MessageBox(msg, 'Select destination directory')
            return

        self.start_manual_btn.Disable()
        self.start_auto_btn.Disable()
        self.stop_manual_btn.Enable()
        self.stop_auto_btn.Disable()

        self._backup()

        return

    def _on_stop_manual(self, event):
        print(self.backup_in_progress)

        if self.backup_in_progress:
            msg = ('This will stop the active backup immediately. This could lead to '
                'some files not being transfered, or being corrupted at the desination. '
                'Proceed?')
            dlg = wx.MessageDialog(self, msg, 'Are you sure?',
                style=wx.CANCEL_DEFAULT|wx.OK|wx.CANCEL|wx.ICON_EXCLAMATION)

            if dlg.ShowModal() == wx.ID_CANCEL:
                return

        self.auto_timer.Stop()

        self._stop_backup()

        if not self.backup_timer.IsRunning():
            self.backup_timer.Start(1)

        return

    def _on_auto_timer(self, event):

        if time.time() - self.start_time >= 60*self.interval and not self.backup_in_progress:
            self.start_time = time.time()
            self._backup()
        elif not self.backup_in_progress:
            m, sec = divmod(60*self.interval - (time.time() - self.start_time), 60)
            self.status.SetLabel('Next backup in {0:.0f}:{1:02.0f}'.format(m, sec))

    def _backup(self):
        if not os.path.exists(self.source):
            msg = 'The source directory no longer exists.'
            wx.MessageBox(msg, 'Source directory missing')
            self.backup_timer.Start(1)
            self.auto_timer.Stop()
            return

        elif not os.path.exists(self.dest):
            msg = 'The destination directory no longer exists.'
            wx.MessageBox(msg, 'Destination directory missing')
            self.backup_timer.Start(1)
            self.auto_timer.Stop()
            return

        self.status.SetLabel('Backing up')

        self.backup_in_progress = True
        self.abort_event.clear()
        self.backup_thread = threading.Thread(target=self._run_rsync)
        self.backup_thread.daemon = True
        self.backup_thread.start()
        self.backup_timer.Start(1000)

        return

    def _on_backup_timer(self, event):
        if not self.backup_in_progress and not self.auto_timer.IsRunning():
            self.start_manual_btn.Enable()
            self.start_auto_btn.Enable()
            self.stop_manual_btn.Disable()
            self.stop_auto_btn.Disable()
            self.backup_timer.Stop()
            self.status.SetLabel('Ready')
        elif not self.backup_in_progress and self.auto_timer.IsRunning():
            self.backup_timer.Stop()

    def _stop_backup(self):
        self.abort_event.set()

    def _run_rsync(self):
        rsync_proc = subprocess.Popen(['rsync', '-avz', self.source, self.dest])

        while rsync_proc.poll() == None:
            if self.abort_event.is_set():
                rsync_proc.terminate()
                break
            time.sleep(.1)

        self.backup_in_progress = False


if __name__ == '__main__':
    # logger = logging.getLogger(__name__)
    # logger.setLevel(logging.DEBUG)
    # h1 = logging.StreamHandler(sys.stdout)
    # h1.setLevel(logging.DEBUG)
    # formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    # h1.setFormatter(formatter)
    # logger.addHandler(h1)

    # my_pump = M50Pump('COM6', '2', 626.2, 9.278)

    # pmp_cmd_q = deque()
    # abort_event = threading.Event()
    # my_pumpcon = PumpCommThread(pmp_cmd_q, abort_event, 'PumpCon')
    # my_pumpcon.start()
    # return_q = queue.Queue()

    # init_cmd = ('connect', ('COM6', 'pump2', 'VICI_M50'),
    #     {'flow_cal': 626.2, 'backlash_cal': 9.278})
    # fr_cmd = ('set_flow_rate', ('pump2', 2000), {})
    # start_cmd = ('start_flow', ('pump2',), {})
    # stop_cmd = ('stop', ('pump2',), {})
    # dispense_cmd = ('dispense', ('pump2', 200), {})
    # aspirate_cmd = ('aspirate', ('pump2', 200), {})
    # moving_cmd = ('is_moving', ('pump2', return_q), {})

    # pmp_cmd_q.append(init_cmd)
    # pmp_cmd_q.append(fr_cmd)
    # pmp_cmd_q.append(start_cmd)
    # pmp_cmd_q.append(dispense_cmd)
    # pmp_cmd_q.append(aspirate_cmd)
    # pmp_cmd_q.append(moving_cmd)
    # time.sleep(5)
    # pmp_cmd_q.append(stop_cmd)
    # my_pumpcon.stop()

    app = wx.App()
    # logger.debug('Setting up wx app')
    frame = TransferFrame(None, title='Data Transfer Control')
    frame.Show()
    app.MainLoop()
