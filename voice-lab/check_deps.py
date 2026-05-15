try:
    import numpy
    print('numpy ok', numpy.__version__)
except Exception as e:
    print('numpy missing', e)
try:
    import scipy
    print('scipy ok', scipy.__version__)
except Exception as e:
    print('scipy missing', e)
