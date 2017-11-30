import wx
import matplotlib
matplotlib.use('WXAgg')
from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg as FigureCanvas
from matplotlib.backends.backend_wx import NavigationToolbar2Wx
from matplotlib.figure import Figure
from ..utils import Plotter
import numpy as np

class plot_gui(wx.Frame):
    def __init__(self, motor_x, motor_y, formula, xlim=None, ylim=None, title=""):
        super(plot_gui, self).__init__(None, title=title)
        self.motor_x = motor_x
        self.motor_y = motor_y
        self.locked = False
        self.initUI()
        self.xlim = xlim
        self.ylim = ylim
        self.setConnections()
        self.x = self.y = self.z = None
        self.plotter = Plotter(self.motor_x, self.motor_y, formula)

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

    def plot(self, output):
        while self.locked:
            continue
        self.locked = True
        if self.plotter.read(output):
            self.update_plot()
        self.locked = False

    def update_plot(self):
        self.x, self.y, self.z = self.plotter.getXYZ()

        if self.x is not None:
            self.axes.cla()
            self.axes.pcolormesh(self.x, self.y, self.z, cmap='jet')

            if self.xlim is not None:
                self.axes.set_xlim(self.xlim)
            if self.ylim is not None:
                self.axes.set_ylim(self.ylim)

            self.canvas.draw()

    def onClicked(self, e):
        """
        Handle when the plot is clicked
        """
        if self.x is None or e.xdata is None or e.ydata is None:
            return

        x = e.xdata
        y = e.ydata

        all_xs = np.arange(len(self.x[0, :]))
        ind_x = min(all_xs, key=lambda i: abs(x-self.x[0][i]))

        all_ys = np.arange(len(self.y[:, 0]))
        ind_y = min(all_ys, key=lambda i: abs(y-self.y[i][0]))

        print 'x=', x,', y=', y,', z=', self.z[ind_y][ind_x]