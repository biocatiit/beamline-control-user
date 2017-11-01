import matplotlib
matplotlib.use('WXAgg')
from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg as FigureCanvas
from matplotlib.backends.backend_wx import NavigationToolbar2Wx
from matplotlib.figure import Figure

import wx
import os
import numpy as np
from os.path import exists, join
from ..utils import Scanner, Plotter
from tifffile import imsave


class bm_scan_ui(wx.Frame):
    def __init__(self, title):
        super(bm_scan_ui, self).__init__(None, title=title)
        self.all_scalars = []
        self.scalar_names = self.getScalars()
        self.xmotors = self.getXMotors()
        self.ymotors = self.getYMotors()
        self.detectors = self.getDetectors()
        self.initUI()
        self.setConnections()
        self.Show()
        self.SetSizeHints((840, 320))
        self.SetSize((840, 330))

    def getScalars(self):
        """
        Query all available scalar name from MX DB
        """
        # HARDCODE AS TEMP
        return ['Io', 'It', 'Iref', 'I1', 'I2', 'I3']

    def getXMotors(self):
        """
        Query all available X motors from MX DB
        """
        # HARDCODE AS TEMP
        return ['smx', 'x1', 'x2', 'x3']

    def getYMotors(self):
        """
        Query all available Y motors from MX DB
        """
        # HARDCODE AS TEMP
        return ['smy', 'y1', 'y2', 'y3']

    def getDetectors(self):
        """
        Query all available detectors name from MX DB
        """
        # HARDCODE AS TEMP
        return ['None', 'Pilatus', 'Det1', 'Det2', 'Det3']


    def initUI(self):
        """
        Initial all ui
        """
        self.main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.panel = wx.Panel(self)
        self.panel_sizer = wx.GridBagSizer(10, 10)

        # Add Motors Settings
        motors_box = wx.StaticBox(self, wx.ID_ANY, "Motors")
        motor_sizer = self.initMotorSizer(motors_box)
        self.panel_sizer.Add(motor_sizer, pos=(0,0), span=(1, 2))

        # Add Scalars Settings
        scalar_box = wx.StaticBox(self, wx.ID_ANY, "Scalars")
        scalar_sizer = self.initScalarSizer(scalar_box)
        self.panel_sizer.Add(scalar_sizer, pos=(0, 2), span=(2, 1),  flag=wx.EXPAND)

        # Add Detectors Settings
        detector_box = wx.StaticBox(self, wx.ID_ANY, "Detector")
        detector_sizer = self.initDetectorSizer(detector_box)
        self.panel_sizer.Add(detector_sizer, pos=(1, 0), span=(1, 2), flag=wx.EXPAND)

        # Add directory field
        self.dir_picker = wx.DirPickerCtrl(self, wx.ID_ANY, path=os.getcwd(), message="Select an output directory")
        self.panel_sizer.Add(wx.StaticText(self, label='Output directory:'), pos=(2, 0), span=(1, 1), flag=wx.EXPAND|wx.ALIGN_BOTTOM)
        self.panel_sizer.Add(self.dir_picker, pos=(2, 1), span=(1, 2), flag=wx.EXPAND)

        # Add start button
        self.start_button = wx.Button(self, wx.ID_ANY, "Start")
        self.panel_sizer.Add(self.start_button, pos=(3, 0), span=(1, 3), flag=wx.ALIGN_CENTER)

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
        self.motorx_start = wx.SpinCtrl(parent=self.panel, style=wx.SP_ARROW_KEYS, min=-10000000, max=10000000, initial=0)
        self.motorx_step = wx.SpinCtrl(parent=self.panel, style=wx.SP_ARROW_KEYS, min=1, max=10000000, initial=1)
        self.motorx_step_size = wx.SpinCtrl(parent=self.panel, style=wx.SP_ARROW_KEYS, min=1, max=10000000, initial=1)

        self.motory_name = wx.ComboBox(self.panel, -1, choices=self.ymotors, style=wx.CB_READONLY)
        self.motory_start = wx.SpinCtrl(parent=self.panel, style=wx.SP_ARROW_KEYS, min=-10000000, max=10000000, initial=0)
        self.motory_step = wx.SpinCtrl(parent=self.panel, style=wx.SP_ARROW_KEYS, min=1, max=10000000, initial=1)
        self.motory_step_size = wx.SpinCtrl(parent=self.panel, style=wx.SP_ARROW_KEYS, min=1, max=10000000, initial=1)

        # Add X
        grid_sizer.Add(wx.StaticText(parent=self, label="Motor X:"), pos=(0, 0), span=(1, 1))
        grid_sizer.Add(self.motorx_name, pos=(0, 1), span=(1, 5))
        grid_sizer.Add(wx.StaticText(parent=self, label="Start:"), pos=(1, 0), span=(1, 1))
        grid_sizer.Add(self.motorx_start, pos=(1, 1), span=(1, 1))
        grid_sizer.Add(wx.StaticText(parent=self, label="Steps:"), pos=(1, 2), span=(1, 1))
        grid_sizer.Add(self.motorx_step, pos=(1, 3), span=(1, 1))
        grid_sizer.Add(wx.StaticText(parent=self, label="Step size:"), pos=(1, 4), span=(1, 1))
        grid_sizer.Add(self.motorx_step_size, pos=(1, 5), span=(1, 1))

        separator = wx.StaticLine(self, -1, style=wx.LI_HORIZONTAL)
        grid_sizer.Add(separator, pos=(2, 0), span=(1, 6), flag=wx.GROW|wx.ALIGN_CENTER_VERTICAL|wx.ALL)

        # Add Y
        grid_sizer.Add(wx.StaticText(parent=self, label="Motor Y:"), pos=(3, 0), span=(1, 1))
        grid_sizer.Add(self.motory_name, pos=(3, 1), span=(1, 5))
        grid_sizer.Add(wx.StaticText(parent=self, label="Start:"), pos=(4, 0), span=(1, 1))
        grid_sizer.Add(self.motory_start, pos=(4, 1), span=(1, 1))
        grid_sizer.Add(wx.StaticText(parent=self, label="Steps:"), pos=(4, 2), span=(1, 1))
        grid_sizer.Add(self.motory_step, pos=(4, 3), span=(1, 1))
        grid_sizer.Add(wx.StaticText(parent=self, label="Step size:"), pos=(4, 4), span=(1, 1))
        grid_sizer.Add(self.motory_step_size, pos=(4, 5), span=(1, 1))

        motor_sizer.Add(grid_sizer)
        return motor_sizer

    def initScalarSizer(self, box):
        """
        Generate Scalar Sizer contains Scalar settings
        :param box: Boxsizer
        :return:
        """
        scalar_sizer = wx.StaticBoxSizer(box, wx.VERTICAL)
        grid_sizer = wx.GridBagSizer(5, 5)
        self.numScalars = wx.SpinCtrl(parent=self.panel, style=wx.SP_ARROW_KEYS, min=1, max=100, initial=1)
        self.dwell_time = wx.SpinCtrlDouble(parent=self.panel, style=wx.SP_ARROW_KEYS, min=0.000000001, max=1000000000, initial=1.0, inc=0.5)
        self.scalar_list_sizer = wx.BoxSizer(wx.VERTICAL)

        grid_sizer.Add(wx.StaticText(parent=self, label="Number of scalars:"), pos=(0, 0), span=(1,2))
        grid_sizer.Add(self.numScalars, pos=(0, 2), span=(1, 1))
        grid_sizer.Add(wx.StaticText(parent=self, label="Dwell time:"), pos=(1, 0), span=(1,1))
        grid_sizer.Add(self.dwell_time, pos=(1, 2), span=(1, 1))
        grid_sizer.Add(self.scalar_list_sizer, pos=(2, 0), span=(3, 3))

        scalar_sizer.Add(grid_sizer)
        self.refreshScalar(None)

        return scalar_sizer

    def initDetectorSizer(self, box):
        """
        Generate Detector Sizer contains Detector settings
        :param box: Boxsizer
        :return:
        """
        det_sizer = wx.StaticBoxSizer(box, wx.VERTICAL)
        grid_sizer = wx.GridBagSizer(5, 5)
        self.detetor = wx.ComboBox(self, -1, choices=self.detectors, style=wx.CB_READONLY)

        grid_sizer.Add(wx.StaticText(parent=self, label="Detector:"), pos=(0, 0), span=(1,2))
        grid_sizer.Add(self.detetor, pos=(0, 2), span=(1, 1))

        det_sizer.Add(grid_sizer)

        return det_sizer

    def setConnections(self):
        """
        Set Handlers to widget events
        """
        self.Bind(wx.EVT_SIZE, self.OnSize)
        self.Bind(wx.EVT_TEXT, self.refreshScalar, self.numScalars)
        self.Bind(wx.EVT_SPINCTRL, self.refreshScalar, self.numScalars)
        self.Bind(wx.EVT_COMBOBOX, self.detectorChanged, self.detetor)
        self.Bind(wx.EVT_BUTTON, self.startPressed, self.start_button)

    def refreshScalar(self, e):
        current = len(self.all_scalars)
        expected = self.numScalars.GetValue()
        if current < expected:
            # Add Scalar if expected number of scalars is higher than current number of scalars
            for i in range(expected - current):
                scalar_items = wx.BoxSizer(wx.HORIZONTAL)
                scalar = wx.ComboBox(self, -1, choices=self.scalar_names, style=wx.CB_READONLY)
                scalar_items.Add(wx.StaticText(self, label=str(current+i+1)+'. '))
                scalar_items.Add(scalar)
                self.all_scalars.append(scalar)
                self.scalar_list_sizer.Add(scalar_items)
                self.main_sizer.Layout()
                self.main_sizer.Fit(self)

        elif current > expected:
            # Remove Scalars from bottom if expected number of scalar is less than current scalars
            for i in range(current-expected):
                self.all_scalars.pop()
                self.scalar_list_sizer.Hide(current - i - 1)
                self.scalar_list_sizer.Remove(current-i-1)
                self.main_sizer.Layout()
                self.main_sizer.Fit(self)

    def detectorChanged(self, e):
        """
        Handle when detector is changed
        """
        print "Current detector is", self.detetor.GetValue()

    def startPressed(self, e):
        """
        Handle when start button is pressed
        """
        if self.checkSettings():
            scalars = [str(s.GetValue()) for s in self.all_scalars]
            dwell_time = self.dwell_time.GetValue()
            detector = {
                'name' : self.detetor.GetValue()
            }

            params = {
                'dir' : str(self.dir_picker.GetPath()),
                'x_motor' : str(self.motorx_name.GetValue()),
                'x_start' : str(self.motorx_start.GetValue()),
                'x_step' : str(self.motorx_step.GetValue()),
                'x_size' : str(self.motorx_step_size.GetValue()),
                'y_motor' : str(self.motory_name.GetValue()),
                'y_start' : str(self.motory_start.GetValue()),
                'y_step' : str(self.motory_step.GetValue()),
                'y_size' : str(self.motory_step_size.GetValue()),
                'scalars' : scalars,
                'dwell_time' : dwell_time,
                'detector' : detector
            }
            scanner = Scanner(**params)
            scanner.generateScanRecord()
            scandata_output = scanner.performScan()

            # TEMP
            scandata_output = '/Users/preawwy/RA/bm_scan/bm_scan/sample/raster.007'

            plotter = Plotter(scandata_output)
            x, y, z = plotter.getPlot()
            if x is not None:
                plot = plot_gui(x, y, z, "raster.007")
                plot.Show()

                ## save image to tif file
                img = np.array(z/z.max()*65535, dtype='uint16')
                imsave(join(self.dir_picker.GetPath(),'result.tif'), img)



    def checkSettings(self):
        # Check settings before running the scan

        # Check scalars
        scalars = []
        for scalar in self.all_scalars:
            scalars.append(str(scalar.GetValue()))

        if len(scalars) != len(set(scalars)):
            print "Error : 2 scalars have the same name"
            return False

        # Check output directory
        dir = self.dir_picker.GetPath()
        if not exists(dir):
            print "Error :",dir," does not exist. Please select another directory."
            return False

        return True

    def OnSize(self, event):
        """
        Handler for window resizing - Do nothing now
        :param event:
        :return:
        """
        pass

class plot_gui(wx.Frame):
    def __init__(self, x, y, z, title=""):
        super(plot_gui, self).__init__(None, title=title)
        self.x = x
        self.y = y
        self.z = z
        self.initUI()
        self.setConnections()
        self.plot()

    def initUI(self):
        """
        Initial all ui
        """
        self.main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.panel = wx.Panel(self)
        self.panel_sizer = wx.BoxSizer(wx.VERTICAL)

        # Add Figure
        self.figure = Figure()
        self.axes = self.figure.add_subplot(111)
        self.canvas = FigureCanvas(self, -1, self.figure)
        self.panel_sizer.Add(self.canvas, 1, wx.LEFT | wx.TOP | wx.EXPAND)

        # Add toolbar
        self.toolbar = NavigationToolbar2Wx(self.canvas)
        self.toolbar.Realize()
        # By adding toolbar in sizer, we are able to put it at the bottom
        # of the frame - so appearance is closer to GTK version.
        self.panel_sizer.Add(self.toolbar, 0, wx.LEFT | wx.EXPAND)
        # update the axes menu on the toolbar
        self.toolbar.update()

        self.panel.SetSizer(self.panel_sizer)
        self.main_sizer.Add(self.panel, 1, wx.GROW)

        self.SetSizer(self.main_sizer)
        self.SetAutoLayout(True)
        self.main_sizer.Fit(self)

    def setConnections(self):
        """
        Set Event Handlers
        """
        self.canvas.mpl_connect('button_press_event', self.onClicked)

    def plot(self):
        self.axes.cla()
        im = self.axes.pcolormesh(self.x, self.y, self.z, cmap='jet')
        self.figure.colorbar(im)
        self.figure.tight_layout()
        self.canvas.draw()

    def onClicked(self, e):
        """
        Handle when the plot is clicked
        """
        print e.xdata, e.ydata

def begin():
    app = wx.App()
    bm_scan_ui('BMScan')
    app.MainLoop()