from __future__ import division

import logging
import threading
import os
from time import time
from copy import deepcopy
from feemodel.config import datadir, windowfillthresh
from feemodel.util import save_obj, load_obj, StoppableThread, proxy
from feemodel.txmempool import MemBlock
from feemodel.estimate.pools import PoolsEstimator

logger = logging.getLogger(__name__)

default_update_period = 86400


class PoolsEstimatorOnline(StoppableThread):

    savedir = os.path.join(datadir, 'pools/')

    def __init__(self, window, update_period=default_update_period):
        self.pools_lock = threading.Lock()
        self.window = window
        self.update_period = update_period
        try:
            self.load_pe()
            assert self.pe
            bestheight = max(self.pe.blockmap)
        except Exception:
            logger.info("Unable to load saved pools; "
                        "starting from scratch.")
            self.pe = PoolsEstimator()
        else:
            if time() - self.pe.timestamp > self.update_period:
                logger.info("Loaded pool estimates are outdated; "
                            "starting from scratch.")
                self.pe = PoolsEstimator()
            else:
                logger.info("Pools Estimator loaded with best height %d." %
                            bestheight)

        self.next_update = self.pe.timestamp + update_period
        self._updating = None
        if not os.path.exists(self.savedir):
            os.mkdir(self.savedir)
        super(PoolsEstimatorOnline, self).__init__()

    @StoppableThread.auto_restart(60)
    def run(self):
        try:
            logger.info("Starting pools online estimator.")
            # TODO: move the windowfill checks into the main loop.
            # Wait for sufficient blocks within the window.
            while not self.is_stopped() and (
                    self._calc_windowfill() < windowfillthresh):
                self.sleep(10)
            logger.info("windowfill is %.2f." % self._calc_windowfill())
            self._updating = False
            self.sleep(max(0, self.next_update-time()))
            while not self.is_stopped():
                self.update()
                self.sleep(max(0, self.next_update-time()))
        except StopIteration:
            pass
        finally:
            logger.info("Stopped pools online estimator.")
            self._updating = None

    def update(self):
        self._updating = True
        currheight = proxy.getblockcount()
        pe = deepcopy(self.pe)
        rangetuple = (currheight-self.window+1, currheight+1)
        pe.start(rangetuple, stopflag=self.get_stop_object())
        self.pe = pe
        self.next_update = pe.timestamp + self.update_period
        try:
            self.save_pe(currheight)
        except Exception:
            logger.exception("Unable to save pools.")
        self._updating = False

    @property
    def pe(self):
        with self.pools_lock:
            return self._pe

    @pe.setter
    def pe(self, val):
        with self.pools_lock:
            self._pe = val

    def load_pe(self):
        savefiles = sorted(os.listdir(self.savedir))
        savefile = os.path.join(self.savedir, savefiles[-1])
        self.pe = load_obj(savefile)

    def save_pe(self, currheight):
        savefilename = 'pe' + str(currheight) + '.pickle'
        savefile = os.path.join(self.savedir, savefilename)
        save_obj(self.pe, savefile)

    @property
    def status(self):
        if self._updating is None:
            return 'stopped'
        elif self._updating:
            return 'running'
        else:
            return 'idle'

    def _calc_windowfill(self):
        '''Calculate window fill ratio.

        Returns the ratio of the number of available memblocks within
        the window, to the window size.
        '''
        numblocks = len(MemBlock.get_heights(window=self.window))
        return numblocks / self.window
