from tifffile import imsave
import matplotlib.pyplot as plt
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


    def produceImage(self, image_file):
        """
        Create an image from scan data
        :param image_file: output file
        :return:
        """
        if self.columns is not None and self.scandata is not None:
            x = sorted(list(set(np.array(self.scandata['smx']))))
            y = sorted(list(set(np.array(self.scandata['smy']))))
            x_coor, y_coor = np.meshgrid(x, y)
            Io = np.array(self.scandata['Io'])
            It = np.array(self.scandata['It'])
            z = It/Io

            intensity = np.reshape(z, (len(y), len(x)))
            fig = plt.figure()
            ax = fig.add_subplot(111)
            ax.cla()
            im = ax.pcolormesh(x_coor, y_coor, intensity)
            # fig.colorbar(im)
            # ax.imshow(intensity)
            fig.tight_layout()
            fig.show()

            # save image to file
            intensity = intensity.astype('float32')
            imsave(image_file, intensity)

