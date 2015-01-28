import threading
import logging
from bisect import insort, bisect
from math import ceil
from contextlib import contextmanager
from random import random
from functools import wraps
from time import time
try:
    import cPickle as pickle
except ImportError:
    import pickle

from bitcoin.rpc import Proxy, JSONRPCException
from bitcoin.wallet import CBitcoinAddress, CBitcoinAddressError


logger = logging.getLogger(__name__)


class StoppableThread(threading.Thread):
    '''A thread with a stop flag.'''

    def __init__(self):
        super(StoppableThread, self).__init__()
        self.__stopflag = threading.Event()

    def stop(self):
        '''Set the stop flag.'''
        self.__stopflag.set()

    def is_stopped(self):
        '''Returns True if stop flag is set, else False.'''
        return self.__stopflag.is_set()

    def sleep(self, secs):
        '''Like time.sleep but terminates immediately once stop flag is set.'''
        self.__stopflag.wait(timeout=secs)

    @contextmanager
    def thread_start(self):
        '''Context manager for starting/closing the thread.

        Starts the thread and terminates it cleanly at the end of the
        context block.
        '''
        self.start()
        try:
            yield
        finally:
            self.stop()
            self.join()

    def get_stop_object(self):
        '''Returns the stop flag.'''
        return self.__stopflag


class BlockingProxy(Proxy):
    '''Thread-safe version of bitcoin.rpc.Proxy.'''

    def __init__(self):
        super(BlockingProxy, self).__init__()
        self.rlock = threading.RLock()

    def _call(self, *args):
        with self.rlock:
            return super(BlockingProxy, self)._call(*args)


class BatchProxy(BlockingProxy):
    '''Proxy with batch calls.'''

    def poll_mempool(self):
        '''Polls mempool for block count and mempool entries.

        Sends getblockcount and getrawmempool requests in batch mode to
        minimize the probability of a race condition in the block count.

        Returns:
            blockcount - output of proxy.getblockcount()
            mempool - output of proxy.getrawmempool(verbose=True)
        '''
        with self.rlock:
            self._RawProxy__id_count += 1
            rpc_call_list = [
                {
                    'version': '1.1',
                    'method': 'getblockcount',
                    'params': [],
                    'id': self._RawProxy__id_count
                },
                {
                    'version': '1.1',
                    'method': 'getrawmempool',
                    'params': [True],
                    'id': self._RawProxy__id_count
                }
            ]

            responses = self._batch(rpc_call_list)
            for response in responses:
                if response['error']:
                    raise JSONRPCException(response['error'])
                if 'result' not in response:
                    raise JSONRPCException({
                        'code': -343, 'message': 'missing JSON-RPC result'
                    })

            return responses[0]['result'], responses[1]['result']


class DataSample(object):
    '''Container for 1-D numerical data with methods to compute statistics.'''

    def __init__(self, datapoints=None):
        '''Specify the initial datapoints with an iterable.'''
        if datapoints:
            self.datapoints = sorted(datapoints)
            self.n = len(self.datapoints)
        else:
            self.datapoints = []
            self.n = None
        self.mean = None
        self.std = None
        self.mean_interval = None

    def add_datapoints(self, datapoints):
        '''Add additional datapoints with an iterable.'''
        for d in datapoints:
            insort(self.datapoints, d)
        self.n = len(self.datapoints)

    def calc_stats(self):
        '''Compute various statistics of the data.

        mean - sample mean
        std - sample standard deviation
        mean_interval - 95% confidence interval for the sample mean, using a
                        normal approximation.
        '''
        if not self.n:
            raise ValueError("No datapoints.")
        self.mean = float(sum(self.datapoints)) / self.n
        variance = (sum([(d - self.mean)**2 for d in self.datapoints]) /
                    (self.n - 1))
        self.std = variance**0.5
        half_interval = 1.96*(variance/self.n)**0.5
        self.mean_interval = (self.mean - half_interval,
                              self.mean + half_interval)

    def get_percentile(self, p, weights=None):
        '''Returns the (p*100)th percentile of the data.

        Optional weights argument is a list specifying the weight of each
        datapoint for computing a weighted percentile.
        '''
        if p > 1 or p < 0:
            raise ValueError("p must be in [0, 1].")
        if not weights:
            return self.datapoints[max(int(ceil(p*self.n)) - 1, 0)]
        elif len(weights) == self.n:
            total = sum(weights)
            target = total*p
            curr_total = 0.
            for idx, s in enumerate(self.datapoints):
                curr_total += weights[idx]
                if curr_total >= target:
                    return s
        else:
            raise ValueError("len(weights) must be equal to len(datapoints).")

    def __repr__(self):
        return "n: %d, mean: %.2f, std: %.2f, interval: %s" % (
            self.n, self.mean, self.std, self.mean_interval)


def save_obj(obj, filename):
    '''Convenience function to pickle an object to disk.'''
    with open(filename, 'wb') as f:
        pickle.dump(obj, f)


def load_obj(filename):
    '''Convenience function to unpickle an object from disk.'''
    with open(filename, 'rb') as f:
        obj = pickle.load(f)
    return obj


def get_coinbase_info(blockheight=None, block=None):
    '''Gets coinbase tag and addresses of a specified block.

    You can either specify a block height, or pass in a
    bitcoin.core.CBlock object.

    Keyword args:
        blockheight - height of block
        block - a bitcoin.core.CBlock object
    Returns:
        addresses - A list of p2sh/p2pkh addresses corresponding to the
                    outputs. Returns None in place of an unrecognizable
                    scriptPubKey.
        tag - the UTF-8 decoded scriptSig.

    '''
    if not block:
        block = proxy.getblock(proxy.getblockhash(blockheight))
    coinbase_tx = block.vtx[0]
    assert coinbase_tx.is_coinbase()
    addresses = []
    for output in coinbase_tx.vout:
        try:
            addr = str(CBitcoinAddress.from_scriptPubKey(output.scriptPubKey))
        except CBitcoinAddressError:
            addr = None
        addresses.append(addr)

    tag = str(coinbase_tx.vin[0].scriptSig).decode('utf-8', 'ignore')

    return addresses, tag


def get_block_timestamp(blockheight):
    '''Get the timestamp of a block specified by height.'''
    block = proxy.getblock(proxy.getblockhash(blockheight))
    return block.nTime


def round_random(f):
    '''Random integer rounding.

    Returns a random integer in {floor(f), ceil(f)} with expected value
    equal to f.
    '''
    int_f = int(f)
    return int_f + (random() <= f - int_f)


def interpolate(x0, x, y):
    '''Linear interpolation of y = f(x) at x0.

     x is assumed sorted.

     Returns:
        y0 - Interpolated value of the function. If x0 is outside of the
             range of x, then return the boundary values.

        idx - The unique value such that x[idx-1] <= x0 < x[idx],
              if it exists. Otherwise idx = 0 if x0 < all values in x,
              and idx = len(x) if x0 >= all values in x.
     '''
    idx = bisect(x, x0)
    if idx == len(x):
        return y[-1], idx
    elif idx == 0:
        return y[0], idx
    else:
        x_f = x[idx]
        y_f = y[idx]
        x_b = x[idx-1]
        y_b = y[idx-1]
        y0 = y_b + float(x0-x_b)/(x_f-x_b)*(y_f-y_b)

        return y0, idx


def try_wrap(fn):
    '''Decorator to try a function and log all exceptions without raising.'''
    @wraps(fn)
    def nicetry(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            logger.exception('try_wrap exception')
    return nicetry


def itertimer(maxiters, maxtime, stopflag):
    '''Generator function which iterates till specified limits.

    maxiters - max number of iterations
    maxtime - max time in seconds that should be spent on the iteration
    stopflag - threading.Event() object. Stop iteration immediately if this
               is set.
    '''
    starttime = time()
    for i in xrange(maxiters):
        if stopflag and stopflag.is_set():
            raise ValueError("Iteration stopped.")
        elapsedtime = time() - starttime
        if elapsedtime > maxtime:
            break
        yield i, elapsedtime


proxy = BatchProxy()
