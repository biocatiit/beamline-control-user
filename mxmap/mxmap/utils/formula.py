def calculate(formula, d):
    """
    Calculate value from string of formula

    :param str formula: Formula e.g. "(x+y)/10"
    :param dict d: Formula variables e.g. {'x':10, 'y':20}

    :returns: calculated result e.g. (10+20)/10 = 3
    :rtype: float
    """
    locals().update(d)
    return eval(formula)
