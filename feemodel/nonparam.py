import threading
from feemodel.config import statsFile, historyFile, config
from feemodel.util import logWrite
from random import choice

leadTimeOffset = config['pollPeriod']
numBootstrap = config['nonparam']['numBootstrap']
numBlocksUsed = config['nonparam']['numBlocksUsed']

class NonParam(object):

    def __init__(self):
        self.blockEstimates = {}
        self.zeroInBlock = []
        self.lock = threading.Lock()

    def pushBlocks(self, blocks):
        with self.lock:
            for block in blocks:
                if not block or not block.entries or block.height in self.blockEstimates:
                # Empty block.entries - means empty mempool. Discard it!
                    continue
                try:
                    minLeadTime = min([entry['leadTime'] for entry in 
                        block.entries.itervalues() if entry['inBlock']])
                except ValueError:
                    self.zeroInBlock.append(block)
                    continue

                self._addBlockEstimate(block,minLeadTime)

            if self.zeroInBlock and len(self.blockEstimates) >= numBlocksUsed[0]:            
                minLeadTimes = [b.minLeadTime for b in self.blockEstimates.values()]
                defaultMLT = minLeadTimes[9*len(minLeadTimes)//10 - 1] # 90th percentile
                for block in self.zeroInBlock:
                    self._addBlockEstimate(block,defaultMLT)
                self.zeroInBlock = []

    def _addBlockEstimate(self,block,minLeadTime):
        # Lock should have been acquired in pushBlocks.
        # To-do: put in an assert that lock is held.
        blockStats = BlockStat(block, minLeadTime)
        feeEstimate = blockStats.estimateFee()
        if feeEstimate:
            self.blockEstimates[block.height] = BlockEstimate(
                block.size, minLeadTime, feeEstimate)
            logWrite('Model: added block ' + str(block.height) + ', %s' %
                self.blockEstimates[block.height])

        # Clean up old blockEstimates
        blockThresh = block.height - numBlocksUsed[1]
        if blockThresh < block.height:
            keysToDelete = [key for key in self.blockEstimates if key <= blockThresh]
            for key in keysToDelete:
                del self.blockEstimates[key]

    def __eq__(self,other):
        if not isinstance(other,NonParam):
            return False
        return self.blockEstimates == other.blockEstimates and self.zeroInBlock == other.zeroInBlock


class BlockStat(object):
    def __init__(self, block, minLeadTime):
        self.entries = block.entries
        self.height = block.height
        self.size = block.size
        self.time = block.time
        self.minLeadTime = minLeadTime
        leadTimeThresh = self.minLeadTime + leadTimeOffset

        # In future perhaps remove high priority
        self.feeStats = [FeeStat(entry) for entry in block.entries.itervalues()
            if self.depsCheck(entry)
            and entry['leadTime'] >= leadTimeThresh
            and entry['feeRate']]
        self.feeStats.sort(key=lambda x: x.feeRate, reverse=True)

    def estimateFee(self):
        if not self.feeStats:
            # No txs which pass the filtering
            return None

        minFeeRate = BlockStat.calcMinFeeRateSingle(self.feeStats)
        
        aboveList = filter(lambda x: x.feeRate >= minFeeRate, self.feeStats)
        belowList = filter(lambda x: x.feeRate < minFeeRate, self.feeStats)

        kAbove = sum([feeStat.inBlock for feeStat in aboveList])
        kBelow = sum([not feeStat.inBlock for feeStat in belowList])

        nAbove = len(aboveList)
        nBelow = len(belowList)

        if minFeeRate != float("inf"):
            altBiasRef = belowList[0].feeRate if nBelow else 0

            bootstrap = [BlockStat.calcMinFeeRateSingle(self.bootstrapSample()) 
                for i in range(numBootstrap)]

            mean = float(sum(bootstrap)) / len(bootstrap)
            std = (sum([(b-mean)**2 for b in bootstrap]) / (len(bootstrap)-1))**0.5

            biasRef = max((minFeeRate, abs(mean-minFeeRate)), 
                (altBiasRef, abs(mean-altBiasRef)), key=lambda x: x[1])[0]
            bias = mean - biasRef
        else:
            bias = float("inf")
            std = float("inf")

        threshFeeStats = aboveList[-10:] + belowList[:10]

        return FeeEstimate(minFeeRate, bias, std, (kAbove,nAbove), (kBelow,nBelow), threshFeeStats)

    def bootstrapSample(self):
        sample = [choice(self.feeStats) for i in range(len(self.feeStats))]
        sample.sort(key=lambda x: x.feeRate, reverse=True)

        return sample


    def depsCheck(self, entry):
        deps = [self.entries.get(depId) for depId in entry['depends']]
        return all([dep['inBlock'] if dep else False for dep in deps])

    @staticmethod
    def calcMinFeeRateSingle(feeStats):
        # feeStats should be sorted by fee rate, reverse=True
        # To-do: Handle empty list (or maybe should be checked earlier)

        kvals = {float("inf"): 0}
        feeRateCurr = float("inf")

        for feeStat in feeStats:
            if feeStat.feeRate < feeRateCurr:
                kvals[feeStat.feeRate] = kvals[feeRateCurr]
                feeRateCurr = feeStat.feeRate

            kvals[feeRateCurr] += 1 if feeStat.inBlock else -1

        maxk = max(kvals.itervalues())
        argmaxk = [feeRate for feeRate in kvals.iterkeys() if kvals[feeRate] == maxk]

        return min(argmaxk)


class FeeEstimate(object):
    def __init__(self, minFeeRate, bias, std, abovekn, belowkn, threshFeeStats):
        self.minFeeRate = minFeeRate
        self.bias = bias
        self.std = std
        self.abovekn = abovekn
        self.belowkn = belowkn
        self.threshFeeStats = threshFeeStats

    def __repr__(self):
        return "FE{mfr: %.1f, bias: %.1f, std: %.1f, above: %s, below: %s}" % (
            self.minFeeRate, self.bias, self.std, self.abovekn, self.belowkn)

class BlockEstimate(object):
    def __init__(self, size, minLeadTime, feeEstimate):
        self.size = size
        self.minLeadTime = minLeadTime
        self.feeEstimate = feeEstimate

    def __repr__(self):
        return "BE{size: %d, mlt: %.1f, %s}" % (self.size, self.minLeadTime, self.feeEstimate)


class FeeStat(object):
    def __init__(self, entry):
        self.feeRate = entry['feeRate']
        self.priority = entry['currentpriority']
        self.size =  entry['size']
        self.inBlock = entry['inBlock']

    def __repr__(self):
        return "FeeStat(%d,%d)" % (self.feeRate,self.inBlock)