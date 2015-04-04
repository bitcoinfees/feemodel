from __future__ import division

import os
import logging
import threading

from feemodel.txmempool import TxMempool
from feemodel.config import datadir
from feemodel.util import load_obj, save_obj
from feemodel.app.pools import PoolsOnlineEstimator
from feemodel.app.txrate import TxRateOnlineEstimator
from feemodel.app.transient import TransientOnline
from feemodel.app.predict import Prediction, pvals_dbfile

logger = logging.getLogger(__name__)

pools_window = 2016
pools_update_period = 86400
pools_minblocks = 432

txrate_halflife = 3600

trans_update_period = 60
trans_miniters = 1000
trans_maxiters = 10000

predict_savefile = os.path.join(datadir, 'savepredict.pickle')
predict_block_halflife = 1008


class SimOnline(TxMempool):

    def __init__(self, predict_savefile=predict_savefile):
        super(SimOnline, self).__init__()
        self.predict_lock = threading.Lock()
        self.predict_savefile = predict_savefile
        self.load_predicts()

        self.poolsonline = PoolsOnlineEstimator(
            pools_window,
            update_period=pools_update_period,
            minblocks=pools_minblocks)
        self.txonline = TxRateOnlineEstimator(halflife=txrate_halflife)
        self.transient = TransientOnline(
            self,
            self.poolsonline,
            self.txonline,
            update_period=trans_update_period,
            miniters=trans_miniters,
            maxiters=trans_maxiters)

    def run(self):
        with self.transient.context_start():
            super(SimOnline, self).run()

    def update(self):
        super(SimOnline, self).update()
        entries, currheight = self.get_entries()
        self.poolsonline.update_async(
            currheight, stopflag=self.get_stop_object())
        threading.Thread(
            target=self.update_predicts,
            args=(entries,)).start()

    def process_blocks(self, *args):
        memblocks = super(SimOnline, self).process_blocks(*args)
        with self.predict_lock:
            self.prediction.process_blocks(memblocks, dbfile=pvals_dbfile)
            self.save_predicts()

    def get_predictstats(self):
        return self.prediction.get_stats()

    def get_transientstats(self):
        return self.transient.stats.get_stats()

    def get_poolstats(self):
        pass

    def update_predicts(self, entries):
        with self.predict_lock:
            self.prediction.update_predictions(
                entries, self.transient.stats)

    def load_predicts(self):
        try:
            self.prediction = load_obj(self.predict_savefile)
        except Exception:
            logger.info("Unable to load saved predicts; "
                        "starting from scratch.")
            self.prediction = Prediction(predict_block_halflife)

    def save_predicts(self):
        try:
            save_obj(self.prediction, self.predict_savefile)
        except Exception:
            logger.info("Unable to save predicts.")


# #class SimOnline(TxMempool):
# #
# #    predict_savefile = os.path.join(datadir, 'savepredicts.pickle')
# #
# #    def __init__(self):
# #        super(SimOnline, self).__init__()
# #        self.process_lock = threading.RLock()
# #        self.predict_lock = threading.Lock()
# #        self.peo = PoolsEstimatorOnline(
# #            pools_config['window'],
# #            update_period=pools_config['update_period'])
# #        self.ss = SteadyStateOnline(
# #            self.peo,
# #            ss_config['window'],
# #            update_period=ss_config['update_period'],
# #            miniters=ss_config['miniters'],
# #            maxiters=ss_config['maxiters'],
# #            maxtime=ss_config['maxtime'])
# #        self.trans = TransientOnline(
# #            self,
# #            self.peo,
# #            trans_config['window'],
# #            update_period=trans_config['update_period'],
# #            miniters=trans_config['miniters'],
# #            maxiters=trans_config['maxiters'],
# #            maxtime=trans_config['maxtime'])
# #        self.load_predicts()
# #
# #    def run(self):
# #        with self.peo.context_start(), self.ss.context_start(), \
# #                self.trans.context_start():
# #            super(SimOnline, self).run()
# #        for thread in threading.enumerate():
# #            if thread.name.startswith('simonline'):
# #                thread.join()
# #
# #    def update(self):
# #        super(SimOnline, self).update()
# #        threading.Thread(target=self.update_predictions,
# #                         name='simonline-updatepredict').start()
# #
# #    def update_predictions(self):
# #        with self.predict_lock:
# #            self.prediction.update_predictions(self.get_entries(),
# #                                               self.trans.stats)
# #
# #    def process_blocks(self, *args, **kwargs):
# #        with self.process_lock:
# #            blocks = super(SimOnline, self).process_blocks(*args, **kwargs)
# #            with self.predict_lock:
# #                self.prediction.process_block(blocks)
# #                try:
# #                    save_obj(self.prediction, self.predict_savefile)
# #                except Exception:
# #                    logger.exception("Unable to save predicts.")
# #
# #    def get_predictscores(self):
# #        with self.predict_lock:
# #            return self.prediction.get_stats()
# #
# #    def load_predicts(self):
# #        try:
# #            self.prediction = load_obj(self.predict_savefile)
# #            assert self.prediction
# #        except Exception:
# #            logger.info("Unable to load saved predicts; "
# #                        "starting from scratch.")
# #            self.prediction = Prediction(predict_feerates, predict_window)
# #        else:
# #            if self.prediction.feerates != predict_feerates:
# #                logger.info("Predict feerates have changed; "
# #                            "starting from scratch.")
# #                self.prediction = Prediction(predict_feerates,
# #                                             predict_window)
# #            else:
# #                numpredicts = len(self.prediction.predicts)
# #                blockscorerange = (min(self.prediction.blockscores),
# #                                   max(self.prediction.blockscores))
# #                logger.info("%d predicts loaded; "
# #                            "block scores in range %s loaded." %
# #                            (numpredicts, blockscorerange))
# #                self.prediction.window = predict_window
# #
# #    def get_status(self):
# #        base_status = super(SimOnline, self).get_status()
# #
# #        peo_status = self.peo.status
# #        ss_status = self.ss.status
# #        trans_status = self.trans.status
# #
# #        status = {
# #            'steadystate': ss_status,
# #            'transient': trans_status,
# #            'poolestimator': peo_status}
# #        status.update(base_status)
# #        return status
