'''
Functions for reading/writing/manipulating textgrid files

Created on Apr 15, 2013

@author: timmahrt
'''

import re
import copy
import functools

import codecs

from praatio.utilities import utils

INTERVAL_TIER = "IntervalTier"
POINT_TIER = "TextTier"


def _morphFunc(fromTier, toTier):
    for fromEntry, toEntry in zip(fromTier.entryList, toTier.entryList):
        
        fromStart, fromEnd, fromLabel = fromEntry
        toStart, toEnd = toEntry[:2]
        
        # Silent pauses are not manipulated to the target destination
        if fromLabel == 'sp' or fromLabel == '':
            tmpStart = fromStart
            tmpEnd = fromEnd
        else:
            tmpStart = toStart
            tmpEnd = toEnd

        yield tmpStart, tmpEnd, fromLabel


def _manipulateFunc(fromTier, modFunc, filterFunc):
    for fromEntry in fromTier.entryList:
        
        fromStart, fromEnd, fromLabel = fromEntry
        
        # Silent pauses are not manipulated to the target destination
        if fromLabel == 'sp' or fromLabel == '' or not filterFunc(fromLabel):
            tmpStart = fromStart
            tmpEnd = fromEnd
        else:
            tmpStart, tmpEnd = modFunc(fromStart, fromEnd)
            
        yield tmpStart, tmpEnd, fromLabel


def _manipulate(tier, iterateFunc):
    '''
    A generic function for manipulating tiers
    
    The provided /iterateFunc/ specifies the new values for old textgrid
    regions
    
    The job of this function is to determine the new location of each textgrid
    intervals (taking into account the other textgrid intervals)
    '''
    # Chains adjustments from prior manipulations onto later ones
    adjustAmount = 0.0
    
    adjustedEntryList = []
    for tmpStart, tmpEnd, fromLabel in iterateFunc():

        tmpAdjustAmount = (tmpEnd - tmpStart)
        
        adjustedStart = adjustAmount
        adjustedEnd = adjustAmount + tmpAdjustAmount
        
        adjustAmount += tmpAdjustAmount
        
        adjustedEntryList.append([adjustedStart, adjustedEnd, fromLabel])
    
    return tier.newTier(tier.name, adjustedEntryList)


def _fillInBlanks(intervalTier, blankLabel="", startTime=None, endTime=None):
    '''
    Fills in the space between intervals with empty space
    
    This is necessary to do when saving to create a well-formed textgrid
    '''
    if startTime is None:
        startTime = intervalTier.minTimestamp
        
    if endTime is None:
        endTime = intervalTier.maxTimestamp
    
    # Special case: empty textgrid
    if len(intervalTier.entryList) == 0:
        intervalTier.entryList.append((startTime, endTime, blankLabel))
    
    newEntryList = fillInBlanks(intervalTier.entryList, blankLabel,
                                startTime, endTime)

    return IntervalTier(intervalTier.name, newEntryList)


def fillInBlanks(entryList, blankLabel="", startTime=0, endTime=None):
    '''
    Fills in an entry list with empty entries-gaps between contentful entries
    '''
    # Create a new entry list
    entry = entryList[0]
    prevEnd = float(entry[1])
    newEntryList = [entry]
    for entry in entryList[1:]:
        newStart = float(entry[0])
        newEnd = float(entry[1])
        
        if prevEnd < newStart:
            newEntryList.append((prevEnd, newStart, blankLabel))
        newEntryList.append(entry)
        
        prevEnd = newEnd
    
    # Special case: If there is a gap at the start of the file
    assert(float(newEntryList[0][0]) >= float(startTime))
    if float(newEntryList[0][0]) > float(startTime):
        newEntryList.insert(0, (startTime, newEntryList[0][0], blankLabel))
    
    # Special case -- if there is a gap at the end of the file
    if endTime is not None:
        assert(float(newEntryList[-1][1]) <= float(endTime))
        if float(newEntryList[-1][1]) < float(endTime):
            newEntryList.append((newEntryList[-1][1], endTime, blankLabel))

    newEntryList.sort()

    return newEntryList


def intervalOverlapCheck(interval, cmprInterval, percentThreshold=0,
                         timeThreshold=0, boundaryInclusive=False):
    '''
    Checks whether two intervals overlap
    
    If percentThreshold is greater than 0, then if the intervals overlap, they
        must overlap by at least this threshold
    
    If timeThreshold is greater than 0, then if the intervals overlap, they
        must overlap by at least this threshold
        
    If boundaryInclusive is true, then two intervals are considered to overlap
        if they share a boundary
    '''
    
    startTime, endTime = interval[:2]
    cmprStartTime, cmprEndTime = cmprInterval[:2]
    
    overlapTime = max(0, min(endTime, cmprEndTime) -
                      max(startTime, cmprStartTime))
    overlapFlag = overlapTime > 0
    
    # Do they share a boundary?  Only need to check if one boundary ends
    # when another begins (because otherwise, they overlap in other ways)
    boundaryOverlapFlag = False
    if boundaryInclusive:
        boundaryOverlapFlag = (startTime == cmprEndTime or
                               endTime == cmprStartTime)
    
    # Is the overlap over a certain percent?
    percentOverlapFlag = False
    if percentThreshold > 0 and overlapFlag:
        totalTime = max(endTime, cmprEndTime) - min(startTime, cmprStartTime)
        percentOverlap = overlapTime / float(totalTime)
        
        percentOverlapFlag = percentOverlap >= percentThreshold
    
    # Is the overlap more than a certain threshold?
    timeOverlapFlag = False
    if timeThreshold > 0 and overlapFlag:
        timeOverlapFlag = overlapTime > timeThreshold
        
    overlapFlag = (overlapFlag or boundaryOverlapFlag or
                   percentOverlapFlag or timeOverlapFlag)
    
    return overlapFlag


class TextgridCollisionException(Exception):
    
    def __init__(self, tierName, insertInterval, collisionList):
        super(TextgridCollisionException, self).__init__()
        self.tierName = tierName
        self.insertInterval = insertInterval
        self.collisionList = collisionList
        
    def __str__(self):
        dataTuple = (str(self.insertInterval),
                     self.tierName,
                     str(self.collisionList))
        return ("Attempted to insert interval %s into tier %s of textgrid" +
                "but overlapping entries %s already exist" % dataTuple)

    
class TimelessTextgridTierException(Exception):
    
    def __str__(self):
        return "All textgrid tiers much have a min and max duration"


class BadIntervalError(Exception):
    
    def __init__(self, start, stop, label):
        super(BadIntervalError, self).__init__()
        self.start = start
        self.stop = stop
        self.label = label
        
    def __str__(self):
        dataTuple = (self.start, self.stop, self.label)
        return ("Problem with interval--could not create textgrid " +
                "(%s,%s,%s)" % dataTuple)
        

class TextgridTier(object):
    
    tierType = None
    
    def __init__(self, name, entryList, minT, maxT):
        self.name = name
        self.entryList = entryList
        self.minTimestamp = minT
        self.maxTimestamp = maxT
    
    def __eq__(self, other):
        def isclose(a, b, rel_tol=1e-14, abs_tol=0.0):
            return abs(a - b) <= max(rel_tol * max(abs(a), abs(b)), abs_tol)
        
        isEqual = True
        isEqual &= self.name == other.name
        isEqual &= self.minTimestamp == other.minTimestamp
        isEqual &= self.maxTimestamp == other.maxTimestamp
        
        for selfEntry, otherEntry in zip(self.entryList, other.entryList):
            for selfSubEntry, otherSubEntry in zip(selfEntry, otherEntry):
                try:
                    isEqual &= isclose(selfSubEntry, otherSubEntry)
                except TypeError:
                    isEqual &= selfSubEntry == otherSubEntry
        
        return isEqual
    
    def appendTier(self, tier, timeRelativeFlag):
        
        if timeRelativeFlag is True:
            appendTier = tier.editTimestamps(self.maxTimestamp,
                                             self.maxTimestamp,
                                             allowOvershoot=True)
        else:
            appendTier = tier
            
        assert(self.tierType == tier.tierType)
        
        entryList = self.entryList + appendTier.entryList
        entryList.sort()
        
        return self.newTier(self.name, entryList)

    def deleteEntry(self, entry):
        '''Removes an entry from the entryList'''
        self.entryList.pop(self.entryList.index(entry))
        
    def editLabels(self, editFunc):
        
        newEntryList = []
        for entry in self.entryList:
            entry[-1] = editFunc(entry[-1])
            if entry[0] < 0.0:  # Toss data that appears before 0 seconds
                continue
            newEntryList.append(entry)
    
        newTier = self.newTier(self.name, newEntryList)
            
        return newTier
    
    def find(self, matchLabel, substrMatchFlag=False):
        '''
        Returns all intervals that match the given label
        '''
        returnList = []
        for entry in self.entryList:
            if not substrMatchFlag:
                if entry[-1] == matchLabel:
                    returnList.append(entry)
            else:
                if matchLabel in entry[-1]:
                    returnList.append(entry)
        
        return returnList
    
    def findRE(self, matchLabel):
        '''
        Returns all intervals that match the given label, using reg. exps.
        '''
        returnList = []
        for entry in self.entryList:
            matchList = re.findall(matchLabel, entry[-1], re.I)
            if matchList != []:
                returnList.append(entry)
        
        return returnList
    
    def getAsText(self):
        '''Prints each entry in the tier on a separate line w/ timing info'''
        text = ""
        text += '"%s"\n' % self.tierType
        text += '"%s"\n' % self.name
        text += '%s\n%s\n%s\n' % (repr(self.minTimestamp),
                                  repr(self.maxTimestamp),
                                  len(self.entryList))
        
        for entry in self.entryList:
            entry = [repr(val) for val in entry[:-1]] + ['"%s"' % entry[-1], ]
            try:
                unicode
            except NameError:
                unicodeFunc = str
            else:
                unicodeFunc = unicode
            
            text += "\n".join([unicodeFunc(val) for val in entry]) + "\n"
            
        return text
        
    def getDuration(self):
        '''Returns the duration of the tier'''
        return self.maxTimestamp - self.minTimestamp
    
    def newTier(self, name=None, entryList=None,
                minTimestamp=None, maxTimestamp=None):
        '''Make a new interval tier derived from the current one'''
        if name is None:
            name = self.name
        if entryList is None:
            entryList = copy.deepcopy(self.entryList)
        if minTimestamp is None:
            minTimestamp = self.minTimestamp
        if maxTimestamp is None:
            maxTimestamp = self.maxTimestamp
        return type(self)(name, entryList, minTimestamp, maxTimestamp)
    
    def sort(self):
        '''Sorts the entries in the entryList'''
        # A list containing tuples and lists will be sorted with tuples
        # first and then lists.  To correctly sort, we need to make
        # sure that all data structures inside the entry list are
        # of the same data type.  The entry list is sorted whenever
        # the entry list is modified, so this is probably the best
        # place to enforce the data type
        self.entryList = [tuple(entry) for entry in self.entryList]
        self.entryList.sort()
        

class PointTier(TextgridTier):
    
    tierType = POINT_TIER
    
    def __init__(self, name, entryList, minT=None, maxT=None):
        
        entryList = [(float(time), label) for time, label in entryList]
        
        # Determine the min and max timestamps
        timeList = [time for time, label in entryList]
        if minT is not None:
            timeList.append(float(minT))
        if maxT is not None:
            timeList.append(float(maxT))
        
        try:
            minT = min(timeList)
            maxT = max(timeList)
        except ValueError:
            raise TimelessTextgridTierException()

        super(PointTier, self).__init__(name, entryList, minT, maxT)

    def crop(self, cropStart, cropEnd):
        '''
        Creates a new tier containing all entries inside the new interval
        '''
        newEntryList = []
        
        for entry in self.entryList:
            timestamp = entry[0]
            
            if timestamp >= cropStart and timestamp <= cropEnd:
                newEntryList.append(entry)

        # Create subtier
        subTier = PointTier(self.name, newEntryList, cropStart, cropEnd)
        return subTier

    def editTimestamps(self, offset, allowOvershoot=False):
        '''
        Modifies all timestamps by a constant amount
        
        If allowOvershoot is True, an interval can go beyond the bounds
        of the textgrid
        '''
        
        newEntryList = []
        for timestamp, label in self.entryList:
            
            newTimestamp = timestamp + offset
            if not allowOvershoot:
                assert(newTimestamp > self.minTimestamp)
                assert(newTimestamp <= self.maxTimestamp)
            
            if newTimestamp < 0:
                continue
            
            newEntryList.append((newTimestamp, label))
        
        # Determine new min and max timestamps
        newMin = min([float(subList[0]) for subList in newEntryList])
        newMax = max([float(subList[1]) for subList in newEntryList])
        
        if newMin > self.minTimestamp:
            newMin = self.minTimestamp
        
        if newMax < self.maxTimestamp:
            newMax = self.maxTimestamp
        
        return PointTier(self.name, newEntryList, newMin, newMax)
    
    def getEntries(self, start=None, stop=None, boundaryInclusive=True):
        '''
        Get all entries for the included range
        '''
        
        if start is None:
            start = self.minTimestamp
        
        if stop is None:
            stop = self.maxTimestamp
        
        returnList = []
        for entry in self.entryList:
            if (boundaryInclusive is True and (entry[0] == start or
                                               entry[0] == stop)):
                returnList.append(entry)
            elif entry[0] > start and entry[0] < stop:
                returnList.append(entry)
        
        return returnList
    
    def insertEntry(self, entry, warnFlag, collisionCode=None):
        '''
        inserts an interval into the tier
        
        collisionCode: in the event that intervals exist in the insertion area,
                        one of three things may happen
        - 'replace' - existing items will be removed
        - 'merge' - inserting item will be fused with existing items
        - None or any other value - TextgridCollisionException is thrown
        
        if warnFlag is True and collisionCode is not None,
        the user is notified of each collision
        '''
        timestamp, label = entry
        
        matchList = []
        entryList = self.getEntries()
        i = None
        for i, searchEntry in entryList:
            if searchEntry[0] == entry[0]:
                matchList.append(searchEntry)
                break
        
        if len(matchList) == 0:
            self.entryList.append(entry)
            
        elif collisionCode.lower() == "replace":
            self.deleteEntry(self.entryList[i])
            self.entryList.append(entry)
            
        elif collisionCode.lower() == "merge":
            oldEntry = self.entryList[i]
            newEntry = (timestamp, "-".join([oldEntry[-1], label]))
            self.deleteEntry(self.entryList[i])
            self.entryList.append(newEntry)
            
        else:
            raise TextgridCollisionException(self.name, entry, matchList)
            
        self.entryList.sort()
        
        if len(matchList) != 0 and warnFlag is True:
            fmtStr = "Collision warning for %s with items %s of tier %s"
            print((fmtStr % (str(entry), str(matchList), self.name)))

        
class IntervalTier(TextgridTier):
    
    tierType = INTERVAL_TIER
    
    def __init__(self, name, entryList, minT=None, maxT=None):
        
        entryList = [(float(start), float(stop), label)
                     for start, stop, label in entryList]
        
        if minT is not None:
            minT = float(minT)
        if maxT is not None:
            maxT = float(maxT)
        
        # Prevent poorly-formed textgrids from being created
        for entry in entryList:
            if entry[0] > entry[1]:
                fmtStr = "Anomaly: startTime=%f, stopTime=%f, label=%s"
                print((fmtStr % (entry[0], entry[1], entry[2])))
            assert(entry[0] < entry[1])
        
        # Remove whitespace
        tmpEntryList = []
        for start, stop, label in entryList:
            tmpEntryList.append((start, stop, label.strip()))
        entryList = tmpEntryList
        
        # Determine the minimum and maximum timestampes
        minTimeList = [subList[0] for subList in entryList]
        maxTimeList = [subList[1] for subList in entryList]
        
        if minT is not None:
            minTimeList.append(minT)
        if maxT is not None:
            maxTimeList.append(maxT)

        try:
            minT = min(minTimeList)
            maxT = max(maxTimeList)
        except ValueError:
            raise TimelessTextgridTierException()
        
        super(IntervalTier, self).__init__(name, entryList, minT, maxT)
        
    def crop(self, cropStart, cropEnd, strictFlag, softFlag):
        '''
        Creates a new tier with all entries that fit inside the new interval
        
        If strictFlag = True, only intervals wholly contained by the crop
            period will be kept
            
        If softFlag = True, the crop period will be stretched to the ends of
            intervals that are only partially contained by the crop period
            
        If both strictFlag and softFlag are set to false, partially contained
            tiers will be truncated in the output tier.
        '''
        newEntryList = []
        cutTStart = 0
        cutTWithin = 0
        cutTEnd = 0
        firstIntervalKeptProportion = 0
        lastIntervalKeptProportion = 0
        
        for entry in self.entryList:
            matchedEntry = None
            
            intervalStart = entry[0]
            intervalEnd = entry[1]
            intervalLabel = entry[2]
            
            # Don't need to investigate if the interval is before or after
            # the crop region
            if intervalEnd <= cropStart or intervalStart >= cropEnd:
                continue
            
            # Determine if the current subEntry is wholly contained
            # within the superEntry
            if intervalStart >= cropStart and intervalEnd <= cropEnd:
                matchedEntry = entry
            
            # If it is only partially contained within the superEntry AND
            # inclusion is 'soft', include it anyways
            elif softFlag and (intervalStart >= cropStart or
                               intervalEnd <= cropEnd):
                matchedEntry = entry
            
            # If not strict, include partial tiers on the edges
            # -- regardless, record how much information was lost
            #        - for strict=True, the total time of the cut interval
            #        - for strict=False, the portion of the interval that lies
            #            outside the new interval

            # The current interval stradles the end of the new interval
            elif intervalStart >= cropStart and intervalEnd > cropEnd:
                cutTEnd = intervalEnd - cropEnd
                lastIntervalKeptProportion = ((cropEnd - intervalStart) /
                                              (intervalEnd - intervalStart))

                if not strictFlag:
                    matchedEntry = (intervalStart, cropEnd, intervalLabel)
                    
                else:
                    cutTWithin += cropEnd - cropStart
            
            # The current interval stradles the start of the new interval
            elif intervalStart < cropStart and intervalEnd <= cropEnd:
                cutTStart = cropStart - intervalStart
                firstIntervalKeptProportion = ((intervalEnd - cropStart) /
                                               (intervalEnd - intervalStart))
                if not strictFlag:
                    matchedEntry = [cropStart, intervalEnd, intervalLabel]
                else:
                    cutTWithin += cropEnd - cropStart

            # The current interval contains the new interval completely
            elif intervalStart <= cropStart and intervalEnd >= cropEnd:
                if not strictFlag:
                    matchedEntry = (cropStart, cropEnd, intervalLabel)
                else:
                    cutTWithin += cropEnd - cropStart
                        
            if matchedEntry is not None:
                newEntryList.append(matchedEntry)

        # Create subtier
        subTier = IntervalTier(self.name, newEntryList, 0, cropEnd - cropStart)
        return (subTier, cutTStart, cutTWithin, cutTEnd,
                firstIntervalKeptProportion, lastIntervalKeptProportion)
         
    def editTimestamps(self, startOffset, stopOffset, allowOvershoot=False):
        '''
        Modifies all timestamps by a constant amount
        
        Can modify the interval start independent of the interval end
        
        If allowOvershoot is True, an interval can go beyond the bounds
        of the textgrid
        '''
        
        newEntryList = []
        for start, stop, label in self.entryList:
            
            newStart = startOffset + start
            newStop = stopOffset + stop
            if allowOvershoot is not True:
                assert(newStart >= self.minTimestamp)
                assert(newStop <= self.maxTimestamp)
            
            if newStop < 0:
                continue
            if newStart < 0:
                newStart = 0
            
            if newStart < 0:
                continue
            
            newEntryList.append((newStart, newStop, label))

        # Determine new min and max timestamps
        newMin = min([entry[0] for entry in newEntryList])
        newMax = max([entry[1] for entry in newEntryList])
        
        if newMin > self.minTimestamp:
            newMin = self.minTimestamp
        
        if newMax < self.maxTimestamp:
            newMax = self.maxTimestamp
        
        return IntervalTier(self.name, newEntryList, newMin, newMax)

    def eraseInterval(self, entry, collisionCode=None):
        '''
        Makes a region in a tier blank (removes all contained entries)
        
        collisionCode: in the event that intervals exist in the insertion area,
                       one of three things may happen
        - 'truncate' - partially contained entries will have the portion
                       removed that overlaps with the target entry
        - 'categorical' - all entries that overlap, even partially, with the
                          target entry will be completely removed
        - None or any other value - AssertionError is thrown
        '''
        startTime, endTime = entry[:2]
        
        matchList = self.getEntries(startTime, endTime)
        
        if len(matchList) == 0:
            pass
        else:
            # There are matches but if the collisionCode is not properly set
            #    it isn't clear what to do
            assert(collisionCode == 'truncate' or
                   collisionCode == 'categorical')
            
            # Remove all the matches from the entryList
            # Go in reverse order because we're destructively altering
            # the order of the list (messes up index order)
            for tmpEntry in matchList[::-1]:
                self.deleteEntry(tmpEntry)
            
            # If we're only truncating, reinsert entries on the left and
            # right edges
            if collisionCode == 'truncate':
                
                if len(matchList) > 1:
                    # Check left edge
                    if matchList[0][0] < startTime:
                        newEntry = (matchList[0][0],
                                    startTime,
                                    matchList[0][-1])
                        self.entryList.append(newEntry)
                        
                    # Check right edge
                    if matchList[-1][1] > endTime:
                        newEntry = (endTime,
                                    matchList[-1][1],
                                    matchList[-1][-1])
                        self.entryList.append(newEntry)
                else:
                    newEntry = entry[:]
                    # Check left edge
                    if matchList[0][0] < startTime:
                        newEntry = (matchList[0][0],
                                    startTime,
                                    matchList[0][-1])
                    # Check right edge
                    if matchList[-1][1] > endTime:
                        newEntry = (endTime,
                                    matchList[-1][1],
                                    matchList[-1][-1])
                    if not newEntry == entry:
                        self.entryList.append(newEntry)

        self.entryList.sort()

    def getEntries(self, start=None, stop=None, boundaryInclusive=False):
        
        if start is None:
            start = self.minTimestamp
        
        if stop is None:
            stop = self.maxTimestamp
        
        targetEntry = (start, stop, "")
        
        returnList = []
        for entry in self.entryList:
            if intervalOverlapCheck(entry, targetEntry, boundaryInclusive):
                returnList.append(entry)
        
        return returnList
    
    def getDurationOfIntervals(self):
        return [float(subList[1]) - float(subList[0])
                for subList in self.entryList]
    
    def getValuesInIntervals(self, dataTupleList, getTimeFunc=None):
        '''
        Returns data from dataTupleList contained in labeled intervals
        
        dataTupleList should be of the form:
        [(time1, value1a, value1b,...), (time2, value2a, value2b...), ...]
        but you can change how time is determined using the getTimeFunc()
        '''
        
        returnList = []
        
        if getTimeFunc is None:
            getTimeFunc = lambda x: x[0]  # Get the first element
        
        for interval in self.entryList:
            intervalDataList = []
            for dataTuple in dataTupleList:
                time = getTimeFunc(dataTuple)
                if interval[0] <= time and interval[1] >= time:
                    intervalDataList.append(dataTuple)
            returnList.append((interval, intervalDataList))
        
        return returnList
            
    def getNonEntries(self):
        '''
        Returns the regions of the textgrid without labels
        
        This can include unlabeled segments and regions marked as silent.
        '''
        entryList = self.getEntries()
        invertedEntryList = [(entryList[i][1], entryList[i + 1][0], "")
                             for i in range(len(entryList) - 1)]
        
        # Remove entries that have no duration (ie lie between two entries
        # that share a border)
        invertedEntryList = [entry for entry in invertedEntryList
                             if entry[0] < entry[1]]
        
        if entryList[0][0] > 0:
            invertedEntryList.insert(0, (0, entryList[0][0], ""))
        
        if entryList[-1][1] < self.maxTimestamp:
            invertedEntryList.append((entryList[-1][1], self.maxTimestamp, ""))
            
        return invertedEntryList
    
    def insertEntry(self, entry, warnFlag, collisionCode=None):
        '''
        inserts an interval into the tier
        
        collisionCode: in the event that intervals exist in the insertion area,
                        one of three things may happen
        - 'replace' - existing items will be removed
        - 'merge' - inserting item will be fused with existing items
        - None or any other value - TextgridCollisionException is thrown
        
        if warnFlag is True and collisionCode is not None,
        the user is notified of each collision
        '''
        startTime, endTime = entry[:2]
        
        matchList = self.getEntries(startTime, endTime)
        
        if len(matchList) == 0:
            self.entryList.append(entry)
            
        elif collisionCode.lower() == "replace":
            for matchEntry in matchList:
                self.deleteEntry(matchEntry)
            self.entryList.append(entry)
            
        elif collisionCode.lower() == "merge":
            for matchEntry in matchList:
                self.deleteEntry(matchEntry)
            matchList.append(entry)
            matchList.sort()  # By starting time
            
            newEntry = [min([entry[0] for entry in matchList]),
                        max([entry[1] for entry in matchList]),
                        "-".join([entry[2] for entry in matchList])]
            self.entryList.append(newEntry)
            
        else:
            raise TextgridCollisionException(self.name, entry, matchList)
            
        self.entryList.sort()
        
        if len(matchList) != 0 and warnFlag is True:
            fmtStr = "Collision warning for %s with items %s of tier %s"
            print((fmtStr % (str(entry), str(matchList), self.name)))

    def manipulate(self, modFunc, filterFunc):
        '''
        
        '''
        return _manipulate(self, functools.partial(_manipulateFunc, self,
                                                   modFunc, filterFunc))
    
    def morph(self, targetTier):
        '''
        Makes one interval tier look more like another
        '''
        return _manipulate(self, functools.partial(_morphFunc,
                                                   self,
                                                   targetTier))
        
class Textgrid():
    
    def __init__(self):
        self.tierNameList = []  # Preserves the order of the tiers
        self.tierDict = {}
    
        self.minTimestamp = None
        self.maxTimestamp = None
    
    def __eq__(self, other):
        isEqual = True
        isEqual &= self.minTimestamp == other.minTimestamp
        isEqual &= self.maxTimestamp == other.maxTimestamp

        isEqual &= self.tierNameList == other.tierNameList
        if isEqual:
            for tierName in self.tierNameList:
                isEqual &= self.tierDict[tierName] == other.tierDict[tierName]
        
        return isEqual
    
    def addTier(self, tier, tierIndex=None):
        
        if tierIndex is None:
            self.tierNameList.append(tier.name)
        else:
            self.tierNameList.insert(tierIndex, tier.name)
            
        assert(tier.name not in list(self.tierDict.keys()))
        self.tierDict[tier.name] = tier
        
        minV = tier.minTimestamp
        if self.minTimestamp is None or minV < self.minTimestamp:
            self.minTimestamp = minV
        
        maxV = tier.maxTimestamp
        if self.maxTimestamp is None or maxV > self.maxTimestamp:
            self.maxTimestamp = maxV
    
    def appendTextgrid(self, tg, onlyMatchingNames=True):
        '''
        Append one textgrid to the end of this one
        
        if onlyMatchingNames is False, tiers that don't appear in both
        textgrids will also appear
        '''
        retTG = Textgrid()
        
        # First add tiers that are in this tg or both tgs
        for name in self.tierNameList:
            sourceTier = self.tierDict[name]
            
            if name in self.tierNameList:
                tier = tg.tierDict[name]
                tier = sourceTier.appendTier(tier, timeRelativeFlag=True)
                retTG.addTier(tier)
            
            elif onlyMatchingNames is False:
                retTG.addTier(tier)
        
        # Second add tiers that are only in the input tg
        if onlyMatchingNames is False:
            for name in tg.tierNameList:
                
                if name not in retTG.tierNameList:
                    tier = tier.offsetTimestamps(self.maxTimestamp,
                                                 self.maxTimestamp)
                    retTG.addTier(tier)
        
        return retTG

    def crop(self, strictFlag, softFlag, startTime=None, endTime=None):
        
        if startTime is None:
            startTime = self.minTimestamp
            
        if startTime is None:
            endTime = self.maxTimestamp
            
        newTG = Textgrid()
        for tierName in self.tierNameList:
            tier = self.tierDict[tierName]
            if isinstance(tier, IntervalTier):
                newTier = tier.crop(startTime, endTime,
                                    strictFlag, softFlag)[0]
            elif isinstance(tier, PointTier):
                newTier = tier.crop(startTime, endTime)
            newTier.sort()
            
            newTG.addTier(newTier)
        
        return newTG

    def editTimestamps(self, startOffset, stopOffset, pointOffset,
                       allowOvershoot=False):
        
        tg = Textgrid()
        for tierName in self.tierNameList:
            tier = self.tierDict[tierName]
            if len(tier.entryList) > 0:
                if isinstance(tier, IntervalTier):
                    tier = tier.editTimestamps(startOffset, stopOffset,
                                               allowOvershoot)
                elif isinstance(tier, PointTier):
                    tier = tier.editTimestamps(pointOffset,
                                               allowOvershoot)
            
            tg.addTier(tier)
        
        return tg
    
    def getContainedLabels(self, superTier):
        '''
        Returns a list of tiers that fall under each label in the superTier
        
        A typical example would be all of the phones in phoneTier that fall
        under each word in wordTier.
        
        Each interval gets its own dictionary of tiers.
        '''
        
        returnList = []
        tier = self.tierDict[superTier]
        for startTime, endTime, label in tier.entryList:
            tierNameList = copy.deepcopy(self.tierNameList)
            tierNameList.pop(tierNameList.index(superTier))
            
            outputDict = {}
            for subTier in tierNameList:
                containedList = []
                tier = self.tierDict[subTier]
                for tmpStart, tmpEnd, label in tier.entryList:
                    if startTime <= tmpStart:
                        if endTime >= tmpEnd:
                            containedList.append((tmpStart, tmpEnd, label))
                        else:
                            break
                outputDict[subTier] = containedList
            returnList.append(outputDict)
            
        return returnList
    
    def getSubtextgrid(self, superTierName, qualifyingFunc, strictFlag):
        '''
        Returns intervals that are inside qualifying superTier intervals
        
        For labeled regions in the super tier that pass the qualifyFunc,
        labeled intervals in the
        
        If /strictFlag/ is True, only intervals wholly contained within the
        textgrid are included.  Otherwise, partially-contained intervals
        will also be included (but truncated to fit within the super tier).
        '''

        superTier = self.tierDict[superTierName]
        tierDataDict = {superTierName: superTier}
        for superEntry in superTier.entryList:
            if qualifyingFunc(superEntry):
                subTG = self.crop(strictFlag, False, superEntry[0],
                                  superEntry[1])
                for subTierName in subTG.tierNameList:
                    if subTierName == superTierName:
                        continue
                    tierDataDict.setdefault(subTierName, [])
                    for subEntry in subTG.tierDict[subTierName]:
                        tierDataDict[subTierName].append(subEntry)
        
        tg = Textgrid()
        for tierName in self.tierNameList:
            tier = self.tierDict[tierName](tierName, tierDataDict[tierName])
            tg.addTier(tier)
            
        return tg

    def mergeTiers(self, includeFunc=None,
                   tierList=None, preserveOtherTiers=True):
        '''
        Combine tiers.
        
        /includeFunc/ regulates which intervals to include in the merging
          with all others being tossed (default tosses silent labels: '')
          
        If /tierList/ is none, combine all tiers.
        '''
        
        if tierList is None:
            tierList = self.tierNameList
            
        if includeFunc is None:
            includeFunc = lambda entryList: not entryList[-1] == ''
           
        # Merge tiers
        superEntryList = []
        for tierName in tierList:
            tier = self.tierDict[tierName]
            superEntryList.extend(tier.entryList)
        
        superEntryList = [entry for entry in superEntryList
                          if includeFunc(entry)]
            
        superEntryList.sort()
        
        # Combine overlapping intervals
        i = 0
        while i < len(superEntryList) - 1:
            currentEntry = superEntryList[i]
            nextEntry = superEntryList[i + 1]
            
            if intervalOverlapCheck(currentEntry, nextEntry):
                currentStart, currentStop, currentLabel = superEntryList[i]
                nextStop, nextLabel = superEntryList.pop(i + 1)[1:]
                
                newStop = max([currentStop, nextStop])
                newLabel = "%s / %s" % (currentLabel, nextLabel)
                
                superEntryList[i] = (currentStart, newStop, newLabel)
                
            else:
                i += 1
            
        # Create the final textgrid
        tg = Textgrid()
            
        # Preserve non-merged tiers
        if preserveOtherTiers is True:
            for tierName in self.tierNameList:
                if tierName not in tierList:
                    tg.addTier(self.tierDict[tierName])

        # Add merged tier
        # (For this we can use any of the tiers involved
        # in the merge to determine the tier type)
        tierName = "/".join(tierList)
        mergedTier = self.tierDict[tierList[0]].newTier(tierName,
                                                        superEntryList)
        tg.addTier(mergedTier)
        
        return tg

    def renameTier(self, oldName, newName):
        oldTier = self.tierDict[oldName]
        tierIndex = self.tierNameList.index(oldName)
        self.removeTier(oldName)
        self.addTier(oldTier.newTier(newName, oldTier.entryList), tierIndex)

    def removeLabels(self, label, tierNameList=None):
        '''Remove labels from tiers'''
        
        # Remove from all tiers if no tiers are specified
        if tierNameList is None:
            tierNameList = self.tierNameList
        
        tg = Textgrid()
        for tierName in self.tierNameList:
            tier = self.tierDict[tierName]
            
            if tierName in tierNameList:
                newEntryList = [entry for entry in tier.entryList
                                if entry[-1] != label]
                tier = tier.newTier(tierName, newEntryList,
                                    tier.minTimestamp, tier.maxTimestamp)
            
            tg.addTier(tier)
        
        return tg
    
    def removeTier(self, name):
        self.tierNameList.pop(self.tierNameList.index(name))
        del self.tierDict[name]

    def replaceTier(self, name, newTierEntryList):
        oldTier = self.tierDict[name]
        tierIndex = self.tierNameList.index(name)
        self.removeTier(name)
        self.addTier(oldTier.newTier(name, newTierEntryList), tierIndex)
            
    def save(self, fn):
        
        self.sort()
        
        # Fill in the blank spaces for interval tiers
        for name in self.tierNameList:
            tier = self.tierDict[name]
            if isinstance(tier, IntervalTier):
                self.tierDict[name] = _fillInBlanks(tier,
                                                    "",
                                                    self.minTimestamp,
                                                    self.maxTimestamp)
        
        self.sort()
        
        # Header
        outputTxt = ""
        outputTxt += 'File type = "ooTextFile short"\n'
        outputTxt += 'Object class = "TextGrid"\n\n'
        outputTxt += "%s\n%s\n" % (repr(self.minTimestamp),
                                   repr(self.maxTimestamp))
        outputTxt += "<exists>\n%d\n" % len(self.tierNameList)
        
        for tierName in self.tierNameList:
            outputTxt += self.tierDict[tierName].getAsText()
        
        codecs.open(fn, "w", encoding="utf-8").write(outputTxt)
    
    def sort(self):
        for name in self.tierNameList:
            self.tierDict[name].sort()


def openTextGrid(fnFullPath):
    
    try:
        data = codecs.open(fnFullPath, "rU", encoding="utf-16").read()
    except UnicodeError:
        data = codecs.open(fnFullPath, "rU", encoding="utf-8").read()
    data = data.replace("\r\n", "\n")
    
    caseA = "ooTextFile short" in data
    caseB = "item" not in data
    if caseA or caseB:
        textgrid = _parseShortTextGrid(data)
    else:
        textgrid = _parseNormalTextGrid(data)
    
    textgrid = textgrid.removeLabels("")
    
    return textgrid


def _parseNormalTextGrid(data):
    '''
    Reads a normal textgrid
    '''
    newTG = Textgrid()
    
    # Toss textgrid header
    data = data.split("item", 1)[1]
    
    # Process each tier individually (will be output to separate folders)
    tierList = data.split("item")[1:]
    for tierTxt in tierList:
        
        if 'class = "IntervalTier"' in tierTxt:
            tierType = INTERVAL_TIER
            searchWord = "intervals"
        else:
            tierType = POINT_TIER
            searchWord = "points"
        
        # Get tier meta-information
        header, tierData = tierTxt.split(searchWord, 1)
        tierName = header.split("name = ")[1].split("\n", 1)[0]
        tierStart = header.split("xmin = ")[1].split("\n", 1)[0]
        tierStart = strToIntOrFloat(tierStart)
        tierEnd = header.split("xmax = ")[1].split("\n", 1)[0]
        tierEnd = strToIntOrFloat(tierEnd)
        tierName = tierName.strip()[1:-1]
        
        # Get the tier entry list
        tierEntryList = []
        labelI = 0
        if tierType == INTERVAL_TIER:
            while True:
                try:
                    timeStart, timeStartI = _fetchRow(tierData,
                                                      "xmin = ", labelI)
                    timeEnd, timeEndI = _fetchRow(tierData,
                                                  "xmax = ", timeStartI)
                    label, labelI = _fetchRow(tierData, "text =", timeEndI)
                except (ValueError, IndexError):
                    break
                
                label = label.strip()
                if label == "":
                    continue
                tierEntryList.append((timeStart, timeEnd, label))
            tier = IntervalTier(tierName, tierEntryList, tierStart, tierEnd)
        else:
            header, tierData = tierTxt.split("points", 1)
            while True:
                try:
                    time, timeI = _fetchRow(tierData, "number = ", labelI)
                    label, labelI = _fetchRow(tierData, "mark =", timeI)
                except (ValueError, IndexError):
                    break
                
                label = label.strip()
                if label == "":
                    continue
                tierEntryList.append((time, label))
            tier = PointTier(tierName, tierEntryList, tierStart, tierEnd)
        
        newTG.addTier(tier)
        
    return newTG


def _parseShortTextGrid(data):
    '''
    Reads a short textgrid file
    '''
    newTG = Textgrid()
    
    intervalIndicies = [(i, True)
                        for i in utils.findAll(data, '"IntervalTier"')]
    pointIndicies = [(i, False) for i in utils.findAll(data, '"TextTier"')]
    
    indexList = intervalIndicies + pointIndicies
    indexList.append((len(data), None))  # The 'end' of the file
    indexList.sort()
    
    tupleList = [(indexList[i][0], indexList[i + 1][0], indexList[i][1])
                 for i in range(len(indexList) - 1)]
    
    for blockStartI, blockEndI, isInterval in tupleList:
        tierData = data[blockStartI:blockEndI]
        
        # First row contains the tier type, which we already know
        metaStartI = _fetchRow(tierData, '', 0)[1]
        
        # Tier meta-information
        tierName, tierNameEndI = _fetchRow(tierData, '', metaStartI)
        tierStartTime, tierStartTimeI = _fetchRow(tierData, '', tierNameEndI)
        tierEndTime, tierEndTimeI = _fetchRow(tierData, '', tierStartTimeI)
        startTimeI = _fetchRow(tierData, '', tierEndTimeI)[1]
        
        tierStartTime = strToIntOrFloat(tierStartTime)
        tierEndTime = strToIntOrFloat(tierEndTime)
        
        # Tier entry data
        entryList = []
        if isInterval:
            while True:
                try:
                    startTime, endTimeI = _fetchRow(tierData, '', startTimeI)
                    endTime, labelI = _fetchRow(tierData, '', endTimeI)
                    label, startTimeI = _fetchRow(tierData, '', labelI)
                except (ValueError, IndexError):
                    break
                
                label = label.strip()
                if label == "":
                    continue
                entryList.append((startTime, endTime, label))
                
            newTG.addTier(IntervalTier(tierName, entryList,
                                       tierStartTime, tierEndTime))
            
        else:
            while True:
                try:
                    time, labelI = _fetchRow(tierData, '', startTimeI)
                    label, startTimeI = _fetchRow(tierData, '', labelI)
                except (ValueError, IndexError):
                    break
                label = label.strip()
                if label == "":
                    continue
                entryList.append((time, label))
                
            newTG.addTier(PointTier(tierName, entryList,
                                    tierStartTime, tierEndTime))

    return newTG


def strToIntOrFloat(inputStr):
    return float(inputStr) if '.' in inputStr else int(inputStr)


def _fetchRow(dataStr, searchStr, index):
    startIndex = dataStr.index(searchStr, index) + len(searchStr)
    endIndex = dataStr.index("\n", startIndex)
    
    word = dataStr[startIndex:endIndex]
    word = word.strip()
    if word[0] == '"' and word[-1] == '"':
        word = word[1:-1]
    word = word.strip()
    
    return word, endIndex + 1
