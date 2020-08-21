#! /usr/bin/env python
# coding: utf-8
#
#    Project: BioCAT staff beamline control software (CATCON)
#             https://github.com/biocatiit/beamline-control-staff
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
import sys
from collections import OrderedDict

import wx

import utils


class ParamPanel(wx.Panel):
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

    def _init_values(self):
        metadata_type = self.settings['metadata_type']

        if metadata_type == 'auto':
            if 'coflow' in self.settings['components']:
                metadata_type = 'saxs'
            elif 'trsaxs_scan' in self.settings['components']:
                metadata_type = 'saxs'
            elif 'trsaxs_flow' in self.settings['components']:
                metadata_type = 'saxs'
            elif 'biocon' in self.settings['components']:
                biocon = wx.FindWindowByName('biocon')

                if 'exposure' in biocon.settings.keys():
                    if biocon.settings['exposure']['tr_muscle_exp']:
                        metadata_type = 'muscle'

        if metadata_type == 'auto':
            metadata_type = 'saxs'

        if metadata_type == 'saxs':
            self.top_sizer.Show(self.saxs_panel, recursive=True)
            self.top_sizer.Hide(self.muscle_panel, recursive=True)

        elif metadata_type == 'muscle':
            self.top_sizer.Hide(self.saxs_panel, recursive=True)
            self.top_sizer.Show(self.muscle_panel, recursive=True)

        self.metadata_type = metadata_type

    def _create_layout(self):

        ctrl_parent = self

        self.saxs_panel = SAXSPanel(self.settings, ctrl_parent)
        self.muscle_panel = MusclePanel(self.settings, ctrl_parent)

        # get_metadata = wx.Button(ctrl_parent, label='Test')
        # get_metadata.Bind(wx.EVT_BUTTON, self._on_get_metadata)

        self.top_sizer = wx.BoxSizer(wx.VERTICAL)
        self.top_sizer.Add(self.saxs_panel, proportion=1, border=5,
            flag=wx.EXPAND|wx.ALL)
        self.top_sizer.Add(self.muscle_panel, proportion=1, border=5,
            flag=wx.EXPAND|wx.ALL)
        # self.top_sizer.Add(get_metadata, flag=wx.ALL, border=5)

        self.SetSizer(self.top_sizer)

    def metadata(self):

        if self.metadata_type == 'saxs':
            metadata = self.saxs_panel.metadata()

        elif self.metadata_type == 'muscle':
            metadata = self.muscle_panel.metadata()

        else:
            metadata = OrderedDict()

        return metadata

    # def _on_get_metadata(self, evt):
    #     metadata = self.metadata()
    #     print(metadata)

    def on_exit(self):
        pass

class SAXSPanel(wx.Panel):
    def __init__(self, settings, *args, **kwargs):
        wx.Panel.__init__(self, *args, **kwargs)

        self.settings = settings

        self._create_layout()
        self._init_values()

    def _init_values(self):
        defaults = self.settings['saxs_defaults']

        self.experiment_type.SetStringSelection(defaults['exp_type'])
        self.buffer.SetValue(defaults['buffer'])
        self.sample.SetValue(defaults['sample'])
        self.temperature.SetValue(str(defaults['temp']))
        self.volume.SetValue(str(defaults['volume']))
        self.concentration.SetValue(str(defaults['conc']))
        self.lc_column_choice.SetStringSelection(defaults['column'])
        self.mixer_type.SetStringSelection(defaults['mixer'])
        self.notes.SetValue(defaults['notes'])

        self._set_experiment_type()

    def _create_layout(self):

        # Should this panel include buffer, sample descriptions as separate fields?
        # Or just let users put that in the notes section?

        top_parent = self

        self.top_sizer = wx.StaticBoxSizer(wx.VERTICAL, top_parent, "SAXS Parameters")
        ctrl_parent = self.top_sizer.GetStaticBox()

        self.experiment_type = wx.Choice(ctrl_parent, choices=['Batch mode SAXS',
            'IEC-SAXS', 'SEC-SAXS', 'SEC-MALS-SAXS', 'TR-SAXS', 'Other'])
        self.experiment_type.Bind(wx.EVT_CHOICE, self._on_experiment_type)

        self.sample = wx.TextCtrl(ctrl_parent)
        self.buffer = wx.TextCtrl(ctrl_parent)

        self.temperature = wx.TextCtrl(ctrl_parent, size=(80, -1),
            validator=utils.CharValidator('float_neg'))

        self.volume = wx.TextCtrl(ctrl_parent, size=(80, -1),
            validator=utils.CharValidator('float'))
        self.concentration = wx.TextCtrl(ctrl_parent, size=(80, -1),
            validator=utils.CharValidator('float'))

        exp_const_sizer = wx.FlexGridSizer(cols=2, vgap=5, hgap=5)
        exp_const_sizer.Add(wx.StaticText(ctrl_parent, label='Experiment type:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        exp_const_sizer.Add(self.experiment_type, flag=wx.ALIGN_CENTER_VERTICAL)
        exp_const_sizer.Add(wx.StaticText(ctrl_parent, label='Sample:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        exp_const_sizer.Add(self.sample, flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        exp_const_sizer.Add(wx.StaticText(ctrl_parent, label='Buffer:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        exp_const_sizer.Add(self.buffer, flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        exp_const_sizer.Add(wx.StaticText(ctrl_parent, label='Temperature [C]:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        exp_const_sizer.Add(self.temperature, flag=wx.ALIGN_CENTER_VERTICAL)
        exp_const_sizer.Add(wx.StaticText(ctrl_parent, label='Loaded volume [uL]:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        exp_const_sizer.Add(self.volume, flag=wx.ALIGN_CENTER_VERTICAL)
        exp_const_sizer.Add(wx.StaticText(ctrl_parent, label='Concentration [mg/ml]:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        exp_const_sizer.Add(self.concentration, flag=wx.ALIGN_CENTER_VERTICAL)

        exp_const_sizer.AddGrowableCol(1)

        column_choices = ['Superdex 200 10/300 Increase', 'Superdex 75 10/300 Increase',
            'Superose 6 10/300 Increase', 'Superdex 200 5/150 Increase',
            'Superdex 75 5/150 Increase', 'Superose 6 5/150 Increase',
            'Superdex 200 10/300', 'Superdex 75 10/300', 'Superose 6 10/300',
            'Superdex 200 5/150', 'Superdex 75 5/150', 'Superose 6 5/150',
            'Wyatt 010S5', 'Wyatt 015S5', 'Wyatt 030S5', 'HiTrap Q FF, 5 ml',
            'HiTrap SP FF, 5 ml', 'Other']

        self.lc_column_choice = wx.Choice(ctrl_parent, choices=column_choices)

        self.lc_sizer = wx.FlexGridSizer(cols=2, vgap=5, hgap=5)
        self.lc_sizer.Add(wx.StaticText(ctrl_parent, label='Column:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.lc_sizer.Add(self.lc_column_choice, flag=wx.ALIGN_CENTER_VERTICAL)


        self.is_buffer = wx.Choice(ctrl_parent, choices=['True', 'False'])
        self.is_buffer.SetSelection(1)

        self.batch_sizer = wx.FlexGridSizer(cols=2, vgap=5, hgap=5)
        self.batch_sizer.Add(wx.StaticText(ctrl_parent, label='Is buffer:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.batch_sizer.Add(self.is_buffer, flag=wx.ALIGN_CENTER_VERTICAL)


        mixers = ['Chaotic S-bend (90 ms)', 'Chaotic S-bend (1 s)',
            'Chaotic S-bend (2 ms)', 'Laminar']
        self.mixer_type = wx.Choice(ctrl_parent, choices=mixers)

        self.tr_sizer = wx.FlexGridSizer(cols=2, vgap=5, hgap=5)
        self.tr_sizer.Add(wx.StaticText(ctrl_parent, label='Mixer:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.tr_sizer.Add(self.mixer_type, flag=wx.ALIGN_CENTER_VERTICAL)


        self.notes = wx.TextCtrl(ctrl_parent, style=wx.TE_MULTILINE, size=(100, 100))

        notes_sizer = wx.BoxSizer(wx.HORIZONTAL)
        notes_sizer.Add(wx.StaticText(ctrl_parent, label='Notes:'))
        notes_sizer.Add(self.notes, proportion=1, border=5,
            flag=wx.EXPAND|wx.LEFT)

        self.top_sizer.Add(exp_const_sizer, border=5,
            flag=wx.LEFT|wx.RIGHT|wx.TOP|wx.EXPAND)
        self.top_sizer.Add(self.lc_sizer, border=5, flag=wx.LEFT|wx.RIGHT|wx.TOP)
        self.top_sizer.Add(self.batch_sizer, border=5, flag=wx.LEFT|wx.RIGHT|wx.TOP)
        self.top_sizer.Add(self.tr_sizer, border=5, flag=wx.LEFT|wx.RIGHT|wx.TOP)
        self.top_sizer.Add(notes_sizer, proportion=1, border=5,
            flag=wx.ALL|wx.EXPAND)

        self.SetSizer(self.top_sizer)

    def _on_experiment_type(self, evt):
        self._set_experiment_type()

    def _set_experiment_type(self):
        exp_type = self.experiment_type.GetStringSelection()

        if exp_type == 'Batch mode SAXS':
            self.top_sizer.Show(self.batch_sizer, recursive=True)
            self.top_sizer.Hide(self.lc_sizer, recursive=True)
            self.top_sizer.Hide(self.tr_sizer, recursive=True)
        elif exp_type == 'TR-SAXS':
            self.top_sizer.Hide(self.batch_sizer, recursive=True)
            self.top_sizer.Hide(self.lc_sizer, recursive=True)
            self.top_sizer.Show(self.tr_sizer, recursive=True)
        elif (exp_type == 'SEC-SAXS' or exp_type == 'SEC-MALS-SAXS' or
            exp_type == 'IEC-SAXS'):
            self.top_sizer.Hide(self.batch_sizer, recursive=True)
            self.top_sizer.Show(self.lc_sizer, recursive=True)
            self.top_sizer.Hide(self.tr_sizer, recursive=True)
        elif exp_type == 'Other':
            self.top_sizer.Hide(self.batch_sizer, recursive=True)
            self.top_sizer.Hide(self.lc_sizer, recursive=True)
            self.top_sizer.Hide(self.tr_sizer, recursive=True)

        self.Layout()

    def metadata(self):
        metadata = OrderedDict()

        exp_type = self.experiment_type.GetStringSelection()

        metadata['Experiment type:'] = exp_type
        metadata['Sample:'] = self.sample.GetValue()
        metadata['Buffer:'] = self.buffer.GetValue()
        metadata['Temperature [C]:'] = self.temperature.GetValue()
        metadata['Loaded volume [uL]:'] = self.volume.GetValue()
        metadata['Concentration [mg/ml]:'] = self.concentration.GetValue()

        if exp_type == 'Batch mode SAXS':

            if self.is_buffer.GetStringSelection() == 'True':
                metadata['Is Buffer:'] = True
                metadata['Concentration [mg/ml]:'] = ''
            else:
                metadata['Is Buffer:'] = False

        elif exp_type == 'TR-SAXS':
            metadata['Mixer:'] = self.mixer_type.GetStringSelection()

        elif (exp_type == 'SEC-SAXS' or exp_type == 'SEC-MALS-SAXS' or
            exp_type == 'IEC-SAXS'):
            metadata['Column:'] = self.lc_column_choice.GetStringSelection()

        metadata['Notes:'] = self.notes.GetValue()

        return metadata

class MusclePanel(wx.Panel):
    def __init__(self, settings, *args, **kwargs):
        wx.Panel.__init__(self, *args, **kwargs)

        self.settings = settings

        self._create_layout()
        self._init_values()

    def _init_values(self):
        defaults = self.settings['muscle_defaults']

        self.system.SetStringSelection(defaults['system'])
        self.muscle_type.SetStringSelection(defaults['muscle_type'])
        self.muscle.SetValue(defaults['muscle'])
        self.preparation.SetStringSelection(defaults['preparation'])
        self.notes.SetValue(defaults['notes'])


    def _create_layout(self):

        # Should this panel include buffer, sample descriptions as separate fields?
        # Or just let users put that in the notes section?

        # Do some smart stuff in the exposure control like checking flow rates
        # vs. column choice and seeing if they're in a reasonable range

        top_parent = self

        self.top_sizer = wx.StaticBoxSizer(wx.VERTICAL, top_parent, "Muscle Parameters")
        ctrl_parent = self.top_sizer.GetStaticBox()

        self.system = wx.ComboBox(ctrl_parent, choices=['Mouse', 'Rat'],
            size=(150, -1))
        self.muscle_type = wx.ComboBox(ctrl_parent, choices=['Cardiac', 'Skeletal'],
            size=(150, -1))
        self.muscle = wx.TextCtrl(ctrl_parent)
        self.preparation = wx.ComboBox(ctrl_parent, choices=['Intact', 'Skinned'],
            size=(150, -1))

        exp_const_sizer = wx.FlexGridSizer(cols=2, vgap=5, hgap=5)
        exp_const_sizer.Add(wx.StaticText(ctrl_parent, label='System:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        exp_const_sizer.Add(self.system, flag=wx.ALIGN_CENTER_VERTICAL)
        exp_const_sizer.Add(wx.StaticText(ctrl_parent, label='Muscle type:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        exp_const_sizer.Add(self.muscle_type, flag=wx.ALIGN_CENTER_VERTICAL)
        exp_const_sizer.Add(wx.StaticText(ctrl_parent, label='Muscle:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        exp_const_sizer.Add(self.muscle, flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        exp_const_sizer.Add(wx.StaticText(ctrl_parent, label='Preparation:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        exp_const_sizer.Add(self.preparation, flag=wx.ALIGN_CENTER_VERTICAL)

        exp_const_sizer.AddGrowableCol(1)


        self.notes = wx.TextCtrl(ctrl_parent, style=wx.TE_MULTILINE, size=(100, 100))

        notes_sizer = wx.BoxSizer(wx.HORIZONTAL)
        notes_sizer.Add(wx.StaticText(ctrl_parent, label='Notes:'))
        notes_sizer.Add(self.notes, proportion=1, border=5,
            flag=wx.EXPAND|wx.LEFT)


        self.top_sizer.Add(exp_const_sizer, border=5,
            flag=wx.LEFT|wx.RIGHT|wx.TOP|wx.EXPAND)
        self.top_sizer.Add(notes_sizer, proportion=1, border=5,
            flag=wx.ALL|wx.EXPAND)

        self.SetSizer(self.top_sizer)


    def metadata(self):
        metadata = OrderedDict()

        metadata['System:'] = self.system.GetValue()
        metadata['Muscle type:'] = self.muscle_type.GetValue()
        metadata['Muscle:'] = self.muscle.GetValue()
        metadata['Preparation:'] = self.preparation.GetValue()
        metadata['Notes:'] = self.notes.GetValue()

        return metadata

class ParamFrame(wx.Frame):
    """
    A lightweight scan frame that holds the :mod:`ParamPanel`.
    """
    def __init__(self, settings, *args, **kwargs):
        """
        Initializes the scan frame. Takes all the usual wx.Frame arguments and
        also the following.

        :param str device_name: The MX record name of the device.
        :param Mp.Record device: The Mp record (i.e. the device)
        :param Mp.Record server_record: The Mp record for the server that the
            device is located on.
        :param Mp.RecordList mx_database: The Mp record list representing the
            MX database being used.
        """
        wx.Frame.__init__(self, *args, **kwargs)

        self._create_layout(settings)

        self.Layout()
        self.Fit()
        self.Layout()

        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _create_layout(self, settings):
        """
        Creates the layout, by calling mod:`ParamPanel`.

        :param str device_name: The MX record name of the device.
        :param Mp.Record device: The Mp record (i.e. the device)
        :param Mp.Record server_record: The Mp record for the server that the
            device is located on.
        :param Mp.RecordList mx_database: The Mp record list representing the
            MX database being used.
        """
        self.scan_panel = ParamPanel(settings, parent=self)

        top_sizer = wx.BoxSizer(wx.HORIZONTAL)
        top_sizer.Add(self.scan_panel, 1, wx.EXPAND)

        self.scan_panel.Layout()
        self.scan_panel.Fit()
        self.scan_panel.Layout()

        self.SetSizer(top_sizer)

    def _on_close(self, evt):
        # self.scan_panel.exit()
        self.Destroy()


if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    # h1.setLevel(logging.INFO)
    h1.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    settings = {
        'components'        : ['metadata'],
        'saxs_defaults'     : {'exp_type'   : 'SEC-SAXS',
                                'buffer'    : '',
                                'sample'    : '',
                                'temp'      : 22,
                                'volume'    : '',
                                'conc'      : '',
                                'column'    : 'Superdex 200 10/300 Increase',
                                'is_buffer' : False,
                                'mixer'     : 'Chaotic S-bend (90 ms)',
                                'notes'     : '',
                                },
        'muscle_defaults'   : {'system'         : 'Mouse',
                                'muscle_type'   : 'Cardiac',
                                'muscle'        : '',
                                'preparation'   : 'Intact',
                                'notes'         : '',
                                },
        'metadata_type'     : 'auto',
        }

    app = wx.App()

    frame = ParamFrame(settings, parent=None, title='Experimental Parameters')
    frame.Show()
    app.MainLoop()

