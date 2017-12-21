import numpy as np

def log(x):
    try:
        res = np.log(x)
    except Exception as e:
        print("Exception : %s . Result will be 0" %(e))
        res = 0
    return res

def calculate(formula, d):
    """
    calculate value from string of formula
    :param formula: string of formula e.g. "(x+y)/10"
    :param d: dictionary of variables e.g. {'x':10, 'y':20}
    :return: calculated result e.g. (10+20)/10 = 3
    """
    locals().update(d)
    return eval(formula)