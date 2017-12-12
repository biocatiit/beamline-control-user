# import matplotlib
# matplotlib.use('WXAgg')

import wx
import os
from os.path import exists
from ..utils.Scanner import Scanner, get_record, get_DB_Path, set_DB_Path
from ..utils.formula import calculate
from plot_gui import plot_gui


class scan_gui(wx.Frame):
    """
    GUI for scanner program. This program allows user to set start, end, and step size for motor x and y. Also, number of scalers and plot formula. (Detectors are not supported now)
    """
    def __init__(self, title):
        super(scan_gui, self).__init__(None, title=title)
        self.all_scalers = []
        self.double_digits = 4
        self.getDevices()
        self.initUI()
        self.setConnections()
        self.Show()
        # self.SetSizeHints((840, 400))
        # self.SetSize((840, 400))

    def getDevices(self):
        """
        Get list of devices from MX Database
        :return:
        """
        self.xmotors = ['smx']
        self.ymotors = ['smy']
        self.scaler_names = ['Io', 'It', 'If']
        self.detectors = ['None']

        return
        self.xmotors = []
        self.ymotors = []
        self.scaler_names = []
        self.detectors = ['None']

        record_list = get_record()
        list_head_record = record_list.list_head_record
        list_head_name = list_head_record.name
        current_record = list_head_record.get_next_record()

        while (current_record.name != list_head_name):
            current_record_class = current_record.get_field('mx_class')
            current_record_superclass = current_record.get_field('mx_superclass')
            current_record_type = current_record.get_field('mx_type')
            # print current_record.name, current_record_class, current_record_superclass, current_record_type

            if current_record_superclass == 'device':
                # ignore a record if it's not a device
                if current_record_class == 'motor':
                    # Add a record to x and y motors
                    self.xmotors.append(current_record.name)
                    self.ymotors.append(current_record.name)
                elif current_record_class == 'scaler':
                    # Add a record to scalers
                    self.scaler_names.append(current_record.name)
                elif current_record_class == 'mca':
                    # Add a record to detectors
                    self.detectors.append(current_record.name)

            current_record = current_record.get_next_record()


    def initUI(self):
        """
        Initial all ui
        """
        self.main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.panel = wx.Panel(self)
        self.panel_sizer = wx.GridBagSizer(10, 10)

        # Add MX DB Path
        init_path = get_DB_Path()
        self.db_picker = wx.FilePickerCtrl(self.panel, wx.ID_ANY, path=init_path, message="Select MX Database")
        self.db_picker.SetPath(init_path)
        self.panel_sizer.Add(wx.StaticText(self.panel, label='MX Database:'), pos=(0, 0), span=(1, 1),
                             flag=wx.ALIGN_CENTER_VERTICAL)
        self.panel_sizer.Add(self.db_picker, pos=(0, 1), span=(1, 2), flag=wx.EXPAND)

        # Add Motors Settings
        motors_box = wx.StaticBox(self.panel, wx.ID_ANY, "Motors")
        motor_sizer = self.initMotorSizer(motors_box)
        self.panel_sizer.Add(motor_sizer, pos=(1,0), span=(1, 2))

        # Add scalers Settings
        scaler_box = wx.StaticBox(self.panel, wx.ID_ANY, "scalers")
        scaler_sizer = self.initscalersizer(scaler_box)
        self.panel_sizer.Add(scaler_sizer, pos=(1, 2), span=(2, 1),  flag=wx.EXPAND)

        # Add Detectors Settings
        detector_box = wx.StaticBox(self.panel, wx.ID_ANY, "Detector")
        detector_sizer = self.initDetectorSizer(detector_box)
        self.panel_sizer.Add(detector_sizer, pos=(2, 0), span=(1, 2), flag=wx.EXPAND)

        # Add directory field
        self.dir_picker = wx.DirPickerCtrl(self.panel, wx.ID_ANY, path=os.getcwd(), message="Select an output directory")
        self.panel_sizer.Add(wx.StaticText(self.panel, label='Output directory:'), pos=(3, 0), span=(1, 1), flag=wx.ALIGN_CENTER_VERTICAL)
        self.panel_sizer.Add(self.dir_picker, pos=(3, 1), span=(1, 2), flag=wx.EXPAND)

        # Add start button
        self.start_button = wx.Button(self.panel, wx.ID_ANY, "Start")
        self.panel_sizer.Add(self.start_button, pos=(4, 0), span=(1, 3), flag=wx.ALIGN_CENTER)

        self.panel.SetSizer(self.panel_sizer)
        self.main_sizer.Add(self.panel, 1, wx.GROW)

        self.SetSizer(self.main_sizer)
        self.SetAutoLayout(True)
        self.main_sizer.Fit(self)

    def initMotorSizer(self, box):
        """
        Generate Motor Sizer contains motor X and Y settings
        :param box: Boxsizer
        :return:
        """
        motor_sizer = wx.StaticBoxSizer(box, wx.VERTICAL)
        grid_sizer = wx.GridBagSizer(5, 5)

        self.motorx_name = wx.ComboBox(self.panel, -1, choices=self.xmotors, style=wx.CB_READONLY)
        if 'smx' in self.xmotors:
            self.motorx_name.SetValue('smx')
        self.motorx_start = wx.SpinCtrlDouble(parent=self.panel, style=wx.SP_ARROW_KEYS, min=-10000000, max=10000000, initial=16000)
        self.motorx_start.SetDigits(self.double_digits)
        self.motorx_end = wx.SpinCtrlDouble(parent=self.panel, style=wx.SP_ARROW_KEYS, min=-10000000, max=10000000, initial=35800)
        self.motorx_end.SetDigits(self.double_digits)
        self.motorx_step = wx.SpinCtrlDouble(parent=self.panel, style=wx.SP_ARROW_KEYS, min=1, max=10000000, initial=600)
        self.motorx_step.SetDigits(self.double_digits)

        self.motory_name = wx.ComboBox(self.panel, -1, choices=self.ymotors, style=wx.CB_READONLY)
        if 'smy' in self.ymotors:
            self.motory_name.SetValue('smy')
        self.motory_start = wx.SpinCtrlDouble(parent=self.panel, style=wx.SP_ARROW_KEYS, min=-10000000, max=10000000, initial=89000)
        self.motory_start.SetDigits(self.double_digits)
        self.motory_end = wx.SpinCtrlDouble(parent=self.panel, style=wx.SP_ARROW_KEYS, min=-10000000, max=10000000, initial=105000)
        self.motory_end.SetDigits(self.double_digits)
        self.motory_step = wx.SpinCtrlDouble(parent=self.panel, style=wx.SP_ARROW_KEYS, min=1, max=10000000, initial=1000)
        self.motory_step.SetDigits(self.double_digits)

        # Add X
        grid_sizer.Add(wx.StaticText(parent=self.panel, label="Motor X:"), pos=(0, 0), span=(1, 1))
        grid_sizer.Add(self.motorx_name, pos=(0, 1), span=(1, 5))
        grid_sizer.Add(wx.StaticText(parent=self.panel, label="Start:"), pos=(1, 0), span=(1, 1))
        grid_sizer.Add(self.motorx_start, pos=(1, 1), span=(1, 1))
        grid_sizer.Add(wx.StaticText(parent=self.panel, label="End:"), pos=(1, 2), span=(1, 1))
        grid_sizer.Add(self.motorx_end, pos=(1, 3), span=(1, 1))
        grid_sizer.Add(wx.StaticText(parent=self.panel, label="Step size:"), pos=(1, 4), span=(1, 1))
        grid_sizer.Add(self.motorx_step, pos=(1, 5), span=(1, 1))

        separator = wx.StaticLine(self.panel, -1, style=wx.LI_HORIZONTAL)
        grid_sizer.Add(separator, pos=(2, 0), span=(1, 6), flag=wx.GROW|wx.ALIGN_CENTER_VERTICAL|wx.ALL)

        # Add Y
        grid_sizer.Add(wx.StaticText(parent=self.panel, label="Motor Y:"), pos=(3, 0), span=(1, 1))
        grid_sizer.Add(self.motory_name, pos=(3, 1), span=(1, 5))
        grid_sizer.Add(wx.StaticText(parent=self.panel, label="Start:"), pos=(4, 0), span=(1, 1))
        grid_sizer.Add(self.motory_start, pos=(4, 1), span=(1, 1))
        grid_sizer.Add(wx.StaticText(parent=self.panel, label="End:"), pos=(4, 2), span=(1, 1))
        grid_sizer.Add(self.motory_end, pos=(4, 3), span=(1, 1))
        grid_sizer.Add(wx.StaticText(parent=self.panel, label="Step size:"), pos=(4, 4), span=(1, 1))
        grid_sizer.Add(self.motory_step, pos=(4, 5), span=(1, 1))

        motor_sizer.Add(grid_sizer)
        return motor_sizer

    def initscalersizer(self, box):
        """
        Generate scaler Sizer contains scaler settings
        :param box: Boxsizer
        :return:
        """
        scaler_sizer = wx.StaticBoxSizer(box, wx.VERTICAL)
        grid_sizer = wx.GridBagSizer(5, 5)
        self.numscalers = wx.SpinCtrl(parent=self.panel, style=wx.SP_ARROW_KEYS|wx.TE_PROCESS_ENTER, min=1, max=100, initial=1)
        self.dwell_time = wx.SpinCtrlDouble(parent=self.panel, style=wx.SP_ARROW_KEYS|wx.TE_PROCESS_ENTER, min=0.000000001, max=1000000000, initial=1.0, inc=0.5)
        self.dwell_time.SetDigits(self.double_digits)
        self.scaler_list_sizer = wx.BoxSizer(wx.VERTICAL)

        init_fomula = ""
        if len(self.scaler_names) > 0:
            if 'Io' in self.scaler_names:
                init_fomula = 'Io'
            else:
                init_fomula = self.scaler_names[0]

        self.formula = wx.TextCtrl(parent=self.panel, value=init_fomula)

        grid_sizer.Add(wx.StaticText(parent=self.panel, label="Number of scalers:"), pos=(0, 0), span=(1,2))
        grid_sizer.Add(self.numscalers, pos=(0, 2), span=(1, 1))
        grid_sizer.Add(wx.StaticText(parent=self.panel, label="Dwell time:"), pos=(1, 0), span=(1,1))
        grid_sizer.Add(self.dwell_time, pos=(1, 2), span=(1, 1))
        grid_sizer.Add(self.scaler_list_sizer, pos=(2, 0), span=(1, 3))
        grid_sizer.Add(wx.StaticText(parent=self.panel, label="Plot Formula:"), pos=(3, 0), span=(1, 1))
        grid_sizer.Add(self.formula, pos=(3, 1), span=(1,2))

        scaler_sizer.Add(grid_sizer)
        self.refreshscaler(None)

        return scaler_sizer

    def initDetectorSizer(self, box):
        """
        Generate Detector Sizer contains Detector settings
        :param box: Boxsizer
        :return:
        """
        det_sizer = wx.StaticBoxSizer(box, wx.VERTICAL)
        grid_sizer = wx.GridBagSizer(5, 5)
        self.detector = wx.ComboBox(self.panel, -1, choices=self.detectors, style=wx.CB_READONLY)
        self.detector.SetValue('None')

        grid_sizer.Add(wx.StaticText(parent=self.panel, label="Detector:"), pos=(0, 0), span=(1,2))
        grid_sizer.Add(self.detector, pos=(0, 2), span=(1, 5), flag = wx.EXPAND)

        det_sizer.Add(grid_sizer)

        return det_sizer

    def setConnections(self):
        """
        Set Handlers to widget events
        """
        self.Bind(wx.EVT_FILEPICKER_CHANGED, self.DBPathChanged, self.db_picker)
        self.Bind(wx.EVT_SIZE, self.OnSize)
        self.Bind(wx.EVT_TEXT, self.refreshscaler, self.numscalers)
        self.Bind(wx.EVT_SPINCTRL, self.refreshscaler, self.numscalers)
        self.Bind(wx.EVT_TEXT_ENTER, self.refreshscaler, self.numscalers)
        self.Bind(wx.EVT_COMBOBOX, self.detectorChanged, self.detector)
        self.Bind(wx.EVT_BUTTON, self.startPressed, self.start_button)

    def DBPathChanged(self, e):
        """
        Handle when DB Path is changed
        """
        set_DB_Path(self.db_picker.GetPath())

        # Get Device list again
        self.getDevices()

        # Refresh Motor X choices
        self.motorx_name.Clear()
        for mx in self.xmotors:
            self.motorx_name.Append(mx)
        self.motorx_name.SetSelection(0)

        # Refresh Motor Y choices
        self.motory_name.Clear()
        for my in self.ymotors:
            self.motory_name.Append(my)
        self.motory_name.SetSelection(0)

        # Refresh Scaler choices
        for scaler in self.all_scalers:
            scaler.Clear()
            for s in self.scaler_names:
                scaler.Append(s)
            scaler.SetSelection(0)

        # Refresh Detector choices
        self.detector.Clear()
        for d in self.detectors:
            self.detector.Append(d)
        self.detector.SetSelection(0)

        # Init Formula
        if len(self.scaler_names) > 0:
            self.formula.SetLabelText(self.scaler_names[0])
        else:
            self.formula.SetLabelText('')

    def refreshscaler(self, e):
        """
        Handle when number of scalar is changed
        """
        current = len(self.all_scalers)
        expected = self.numscalers.GetValue()
        if current < expected:
            # Add scaler if expected number of scalers is higher than current number of scalers
            for i in range(expected - current):
                scaler_items = wx.BoxSizer(wx.HORIZONTAL)
                scaler = wx.ComboBox(self.panel, -1, choices=self.scaler_names, style=wx.CB_READONLY)
                if len(self.scaler_names) > 0:
                    if 'Io' in self.scaler_names:
                        scaler.SetValue('Io')
                    else:
                        scaler.SetValue(self.scaler_names[0])
                scaler_items.Add(wx.StaticText(self.panel, label=str(current+i+1)+'. '))
                scaler_items.Add(scaler, flag = wx.EXPAND)
                self.all_scalers.append(scaler)
                self.scaler_list_sizer.Add(scaler_items)
                self.main_sizer.Layout()
                self.main_sizer.Fit(self.panel)

        elif current > expected:
            # Remove scalers from bottom if expected number of scaler is less than current scalers
            for i in range(current-expected):
                self.all_scalers.pop()
                self.scaler_list_sizer.Hide(current - i - 1)
                self.scaler_list_sizer.Remove(current-i-1)
                self.main_sizer.Layout()
                self.main_sizer.Fit(self)

    def detectorChanged(self, e):
        """
        Handle when detector is changed
        """
        print("Current detector is "+str(self.detector.GetValue()))

    def startPressed(self, e):
        """
        Handle when start button is pressed
        """
        if self.checkSettings():
            scalers = [str(s.GetValue()) for s in self.all_scalers]
            dwell_time = self.dwell_time.GetValue()
            if self.detector.GetValue() == 'None':
                detector = None
            else:
                detector = {
                    'name' : self.detector.GetValue()
                }

            params = {
                'dir' : str(self.dir_picker.GetPath()),
                'x_motor' : str(self.motorx_name.GetValue()),
                'x_start' : self.motorx_start.GetValue(),
                'x_step' : self.motorx_step.GetValue(),
                'x_end' : self.motorx_end.GetValue(),
                'y_motor' : str(self.motory_name.GetValue()),
                'y_start' : self.motory_start.GetValue(),
                'y_step' : self.motory_step.GetValue(),
                'y_end' : self.motory_end.GetValue(),
                'scalers' : scalers,
                'dwell_time' : dwell_time,
                'detector' : detector
            }


            plot_panel = plot_gui(motor_x=params['x_motor'], motor_y=params['y_motor'], formula=self.formula.GetValue(),
                            xlim=(params['x_start'], params['x_end'], params['x_step']), ylim=(params['y_start'], params['y_end'], params['y_step']))
            plot_panel.Show()

            params['callback'] = plot_panel

            scanner = Scanner(**params)
            scanner.generateScanRecord()
            scanner.performScan()

            ## save image to tif file
            # img = np.array(z/z.max()*65535, dtype='uint16')
            # imsave(join(self.dir_picker.GetPath(),'result.tif'), img)

    def checkSettings(self):
        # Check settings before running the scan

        # Check scalers
        scalers = []
        d_scalers = {}
        for scaler in self.all_scalers:
            scalers.append(str(scaler.GetValue()))
            d_scalers[str(scaler.GetValue())] = 1.0

        if len(scalers) != len(set(scalers)):
            print("Error : 2 scalers have the same name")
            return False

        # Check fomula
        try:
            calculate(str(self.formula.GetValue()), d_scalers)
        except:
            print("Error : Invalid Formula")
            return False

        # Check output directory
        dir = self.dir_picker.GetPath()
        if not exists(dir):
            print("Error :",dir," does not exist. Please select another directory.")
            return False

        return True

    def OnSize(self, event):
        """
        Handler for window resizing - Do nothing now
        :param event:
        :return:
        """
        pass

def begin():
    app = wx.App()
    scan_gui('BMScan')
    app.MainLoop()