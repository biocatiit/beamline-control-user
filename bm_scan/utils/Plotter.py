import pandas as pd
import numpy as np
from os.path import exists, join
from formula import calculate
import time

class Plotter:
    """
    A class to create a plot (image) for scan data which performs by Scanner
    """
    def __init__(self, motor_x, motor_y, formula):
        self.motor_x = motor_x
        self.motor_y = motor_y
        self.formula = formula
        self.columns = None
        self.scandata = None

    def read(self, full_path):
        """
        Read scan data file and encapsulate data
        """
        if not exists(full_path):
            # time.sleep(2)
            print full_path, "does not exist"
            return False

        data = np.loadtxt(full_path)
        self.columns = get_cols(full_path)

        scandata = pd.DataFrame(data, columns=self.columns)
        if self.scandata is None:
            self.scandata = scandata
        else:
            self.scandata = pd.concat([self.scandata, scandata])

        self.scandata = self.scandata.sort_values(by=[self.motor_y, self.motor_x]) # TODO
        self.scandata = self.scandata.drop_duplicates(keep='first')
        return True

    def getXYZ(self):
        """
        Create map from scan data, display map, and save map as an image to a file
        :param image_file: output filename
        :return:
        """

        if self.scandata is not None:
            scan_data = self.scandata.copy()
            x = sorted(list(set(np.array(scan_data[self.motor_x]))))
            y = sorted(list(set(np.array(scan_data[self.motor_y]))))
            x_coor, y_coor = np.meshgrid(x, y)
            d = {}
            for c in self.columns:
                d[c] = np.array(scan_data[c])
            z = calculate(self.formula, d)

            z = np.reshape(z, (len(y), len(x)))
            return x_coor, y_coor, z
        else:
            return None, None, None

def get_cols(full_path):
    file = open(full_path, 'r')
    # Get column names
    cols = []
    for line in file:
        if "%devices" in line:
            toks = line.split()
            equal_ind = toks.index('=')
            for i in range(equal_ind + 1, len(toks)):
                c = toks[i]
                if len(c) > 0:
                    c = c.rstrip('\n')
                    c = c.rstrip(';')
                    cols.append(c)
            break
    return cols
