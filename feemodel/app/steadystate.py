from __future__ import division

import logging
import threading
import os
from copy import deepcopy
from time import time

from feemodel.config import datadir
from feemodel.util import save_obj, load_obj, proxy
from feemodel.util import StoppableThread, DataSample
from feemodel.estimate.txrate import TxRateEstimator
from feemodel.simul import Simul
from feemodel.simul.stats import SimStats, get_feeclasses
from feemodel.waitmeasure import WaitMeasure
from feemodel.queuestats import QueueStats

logger = logging.getLogger(__name__)

tx_maxsamplesize = 100000
default_update_period = 86400
default_miniters = 100000
default_maxiters = float("inf")
default_maxtime = 600


class SteadyStateOnline(StoppableThread):

    savedir = os.path.join(datadir, 'steadystate')

    def __init__(self, peo, window, update_period=default_update_period,
                 miniters=default_miniters, maxiters=default_maxiters,
                 maxtime=default_maxtime):
        self.stats_lock = threading.Lock()
        self.peo = peo
        self.window = window
        self.update_period = update_period
        self.miniters = miniters
        self.maxiters = maxiters
        self.maxtime = maxtime
        try:
            self.load_stats()
            assert self.stats
        except Exception:
            logger.info("Unable to load saved stats.")
            self.stats = SteadyStateStats()
        else:
            if time() - self.stats.timestamp > self.update_period:
                logger.info("Loaded stats are outdated; "
                            "starting from scratch.")
                self.stats = SteadyStateStats()
            else:
                logger.info("Steady-state stats loaded.")

        self.next_update = self.stats.timestamp + update_period
        self._updating = None
        if not os.path.exists(self.savedir):
            os.mkdir(self.savedir)
        super(SteadyStateOnline, self).__init__()

    @StoppableThread.auto_restart(60)
    def run(self):
        try:
            self._updating = False
            logger.info("Starting steady-state online sim.")
            self.sleep(max(0, self.next_update-time()))
            while not self.peo.pe and not self.is_stopped():
                self.sleep(10)
            while not self.is_stopped():
                self.update()
                self.sleep(max(0, self.next_update-time()))
        except StopIteration:
            pass
        finally:
            logger.info("Stopped steady-state online sim.")
            self._updating = None

    def update(self):
        self._updating = True
        stats = deepcopy(self.stats)
        stats.timestamp = time()

        currheight = proxy.getblockcount()
        blockrangetuple = (currheight-self.window+1, currheight+1)
        tx_source = TxRateEstimator(maxsamplesize=tx_maxsamplesize)
        tx_source.start(blockrangetuple, stopflag=self.get_stop_object())

        pools = deepcopy(self.peo.pe)
        assert pools
        pools.calc_blockrate()

        # TODO: catch unstable error
        sim = Simul(pools, tx_source)
        feeclasses = get_feeclasses(sim.cap, tx_source, sim.stablefeerate)
        self.simulate(sim, feeclasses, stats)

        if feeclasses != stats.waitmeasure.feerates:
            stats.waitmeasure = WaitMeasure(feeclasses)
        stats.waitmeasure.calcwaits(blockrangetuple,
                                    stopflag=self.get_stop_object())

        self.stats = stats
        self.next_update = stats.timestamp + self.update_period
        try:
            self.save_stats(currheight)
        except Exception:
            logger.exception("Unable to save steady-state stats.")
        self._updating = False

    def simulate(self, sim, feeclasses, stats):
        qstats = QueueStats(feeclasses)
        qshortstats = QueueStats(feeclasses)
        shortstats = {feerate: DataSample() for feerate in feeclasses}

        logger.info("Beginning steady-state simulation..")
        for block, realtime in sim.run():
            if self.is_stopped():
                raise StopIteration
            if block.height >= self.maxiters or (
                    block.height >= self.miniters and
                    realtime > self.maxtime):
                break
            qstats.next_block(block.height, block.interval, block.sfr)
            qshortstats.next_block(block.height, block.interval, block.sfr)
            if not (block.height + 1) % self.window:
                for queueclass in qshortstats.stats:
                    shortstats[queueclass.feerate].add_datapoints(
                        [queueclass.avgwait])
                qshortstats = QueueStats(feeclasses)
        logger.info("Finished steady-state simulation in %.2fs "
                    "and %d iterations." % (realtime, block.height))
        # Warn if we reached miniters
        if block.height == self.miniters:
            logger.warning("Steadystate sim took %.2fs to do %d iters." %
                           (realtime, block.height))

        stats.qstats = qstats
        stats.shortstats = shortstats
        stats.timespent = realtime
        stats.numiters = block.height
        stats.cap = sim.cap
        stats.stablefeerate = sim.stablefeerate

    @property
    def stats(self):
        with self.stats_lock:
            return self._stats

    @stats.setter
    def stats(self, val):
        with self.stats_lock:
            self._stats = val

    def load_stats(self):
        savefiles = sorted(os.listdir(self.savedir))
        savefile = os.path.join(self.savedir, savefiles[-1])
        self.stats = load_obj(savefile)
        # Put in the loaded info

    def save_stats(self, currheight):
        savefilename = 'ss' + str(currheight) + '.pickle'
        savefile = os.path.join(self.savedir, savefilename)
        save_obj(self.stats, savefile)

    @property
    def status(self):
        if self._updating is None:
            return 'stopped'
        elif self._updating:
            return 'running'
        else:
            return 'idle'


class SteadyStateStats(SimStats):
    def __init__(self):
        self.qstats = None
        self.shortstats = None
        self.waitmeasure = WaitMeasure([])
        super(SteadyStateStats, self).__init__()

    def print_stats(self):
        super(SteadyStateStats, self).print_stats()
        if self.qstats:
            self.qstats.print_stats()