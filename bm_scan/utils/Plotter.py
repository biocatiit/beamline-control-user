import pandas as pd
import numpy as np

class Plotter:
    """
    A class to create a plot (image) for scan data which performs by Scanner
    """
    def __init__(self, filename):
        self.filename = filename
        self.columns = None
        self.scandata = None
        self.read()

    def read(self):
        """
        Read scan data file and encapsulate data
        """
        # Get column names
        file = open(self.filename, 'r')
        cols = []
        for line in file:
            if "%devices" in line:
                toks = line.split()
                equal_ind = toks.index('=')
                for i in range(equal_ind+1, len(toks)):
                    c = toks[i]
                    if len(c) > 0:
                        c = c.rstrip('\n')
                        c = c.rstrip(';')
                        cols.append(c)
                break

        # Read data
        if len(cols) > 0:
            self.columns = cols
            data = np.loadtxt(self.filename)
            self.scandata = pd.DataFrame(data, columns=self.columns)
        else:
            print "Error : there are no column names"


    def getPlot(self):
        """
        Create map from scan data, display map, and save map as an image to a file
        :param image_file: output filename
        :return:
        """

        if self.scandata is not None:
            x = sorted(list(set(np.array(self.scandata['smx']))))
            y = sorted(list(set(np.array(self.scandata['smy']))))
            x_coor, y_coor = np.meshgrid(x, y)
            Io = np.array(self.scandata['Io'])
            It = np.array(self.scandata['It'])
            z = It/Io
            z = np.reshape(z, (len(y), len(x)))
            return x_coor, y_coor, z
        else:
            return None, None, None
