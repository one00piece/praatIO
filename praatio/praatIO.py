'''
Created on Apr 15, 2013

@author: timmahrt
'''

import re
import copy
import functools

import codecs

from os.path import join

# Can only handle interval tiers at the moment
INTERVAL_TIER = "IntervalTier"
POINT_TIER = "TextTier"


def _morphFunc(fromTier, toTier):
    for fromEntry, toEntry in zip(fromTier.entryList, toTier.entryList):
        
        fromStart, fromEnd, fromLabel = fromEntry
        toStart, toEnd, toLabel = toEntry
        
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
    
    The provided /iterateFunc/ specifies the new values for old textgrid regions
    
    The job of this function is to determine the new location of each textgrid
    intervals (taking into account the other textgrid intervals)
    '''
    adjustedEntryList = []
    adjustAmount = 0.0 # Chains adjustments from prior manipulations onto later ones
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
    if startTime == None:
        startTime = intervalTier.minTimestamp
        
    if endTime == None:
        endTime = intervalTier.maxTimestamp
    
    # Special case: empty textgrid
    if len(intervalTier.entryList) == 0:
        intervalTier.entryList.append( (startTime, endTime, blankLabel))
    
    # Create a new entry list
    entry = intervalTier.entryList[0]
    prevEnd = float(entry[1])
    newEntryList = [entry]
    for entry in intervalTier.entryList[1:]:
        newStart = float(entry[0])
        newEnd = float(entry[1])
        
        if prevEnd < newStart:
            newEntryList.append( (prevEnd, newStart, blankLabel) )
        newEntryList.append(entry)
        
        prevEnd = newEnd
    
    # Special case: If there is a gap at the start of the file
    assert( float(newEntryList[0][0]) >= float(startTime) )
    if float(newEntryList[0][0]) > float(startTime):
        newEntryList.insert(0, (startTime, newEntryList[0][0], blankLabel))
    
    # Special case -- if there is a gap at the end of the file
    assert( float(newEntryList[-1][1]) <= float(endTime) )
    if float(newEntryList[-1][1]) < float(endTime):
        newEntryList.append( (newEntryList[-1][1], endTime, blankLabel) ) 

    newEntryList.sort()

    return IntervalTier(intervalTier.name, newEntryList)
    

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
    
    startTime, endTime, label = interval
    cmprStartTime, cmprEndTime, cmprLabel = cmprInterval
    
    overlapTime = max(0, min(endTime, cmprEndTime) - max(startTime, cmprStartTime))
    overlapFlag = overlapTime > 0
    
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
        
    overlapFlag = overlapFlag or percentOverlapFlag or timeOverlapFlag
    
    return overlapFlag


class TextgridCollisionException(Exception):
    
    def __init__(self, tierName, insertInterval, collisionList):
        self.tierName = tierName
        self.insertInterval = insertInterval
        self.collisionList = collisionList
        
    def __str__(self):
        return "Attempted to insert interval %s into tier %s of textgrid but overlapping entries %s already exist" % (str(self.insertInterval), self.tierName, str(self.collisionList))
    
    
class TimelessTextgridTierException(Exception):
    
    def __str__(self):
        return "All textgrid tiers much have a min and max duration"


class BadIntervalError(Exception):
    
    def __init__(self, start, stop, label):
        self.start = start
        self.stop = stop
        self.label = label
        
    def __str__(self):
        return "Problem with interval--could not create textgrid (%s,%s,%s)" % (self.start, self.stop, self.label)



class TextgridTier(object):
    
    
    def __init__(self, name, entryList):
        self.name = name
        self.entryList = entryList
        

    def appendTier(self, tier, timeRelativeFlag):
        
        if timeRelativeFlag == True:
            appendTier = tier.editTimestamps(self.maxTimestamp, self.maxTimestamp, allowOvershoot=True)
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
        Returns all intervals that match the given label, using regular expressions
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
        text += '%s\n%s\n%s\n' % (self.minTimestamp, self.maxTimestamp, len(self.entryList))
        
        for entry in self.entryList:
            entry = entry[:-1] + ('"%s"' % entry[-1],)
            text += "\n".join([str(val) for val in entry]) + "\n"
            
        return text
    
    
    def getDuration(self):
        '''Returns the duration of the tier'''
        return self.maxTimestamp - self.minTimestamp

    
    def newTier(self, name, entryList, minTimestamp=None, maxTimestamp=None):
        '''Returns a new instance of the same type of tier as self'''
        raise NotImplementedError()
    
    
    def sort(self):
        '''Sorts the entries in the entryList'''
        self.entryList.sort()
        

class PointTier(TextgridTier):
    
    tierType = POINT_TIER
    
    def __init__(self, name, entryList, minT=None, maxT=None):
        self.name = name
        if minT != None:
            minT = float(minT)
        if maxT != None:
            maxT = float(maxT)
        self.entryList = [(float(time), label) for time, label in entryList]
        
        # Determine the min and max timestamps
        timeList = [time for time, label in entryList]
        if minT != None:
            timeList.append(minT)
        if maxT != None:
            timeList.append(maxT)
        
        try:
            self.minTimestamp = min(timeList)
            self.maxTimestamp = max(timeList)
        except ValueError:
            raise TimelessTextgridTierException()
        

    def crop(self, cropStart, cropEnd):
        '''
        Creates a new tier containing all entries that fit inside a new interval
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
            
            newEntryList.append( (newTimestamp, label) )
        
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
        
        if start == None:
            start = self.minTimestamp
        
        if stop == None:
            end = self.maxTimestamp
        
        returnList = []
        for entry in self.entryList:
            if boundaryInclusive == True and (entry[0] == start or entry[0] == stop):
                returnList.append(entry)
            elif entry[0] > start and entry[0] < stop:
                returnList.append(entry)
        
        return returnList
    
    
    def insert(self, entry, warnFlag, collisionCode=None):
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
            self.entryList.append(entry)
            
        else:
            raise TextgridCollisionException(self.name, entry, matchList)
            
        self.entryList.sort()
        
        if len(matchList) != 0 and warnFlag == True:
            print "Collision warning for %s with items %s of tier %s" % (str(entry),
                                                                       str(matchList),
                                                                       self.name)
    
    
    def newTier(self, name, entryList, minTimestamp=None, maxTimestamp=None):
        if minTimestamp == None:
            minTimestamp = self.minTimestamp
        if maxTimestamp == None:
            maxTimestamp = self.maxTimestamp
        return PointTier(name, entryList, minTimestamp, maxTimestamp)
    

        
class IntervalTier(TextgridTier):
    
    tierType = INTERVAL_TIER
    
    def __init__(self, name, entryList, minT=None, maxT=None):
        
        entryList = [(float(start), float(stop), label) 
                     for start, stop, label in entryList]
        
        if minT != None:
            minT = float(minT)
        if maxT != None:
            maxT = float(maxT)
        
        # Prevent poorly-formed textgrids from being created
        for entry in entryList:
            if entry[0] > entry[1]:
                print "Anomaly: startTime=%f, stopTime=%f, label=%s" % (entry[0], entry[1], entry[2])
            assert(entry[0] < entry[1])
        
        # Remove whitespace
        tmpEntryList = []
        for start, stop, label in entryList:
            tmpEntryList.append( (start, stop, label.strip()))
        entryList = tmpEntryList
        
        self.name = name
        self.entryList = entryList
        
        # Determine the minimum and maximum timestampes
        minTimeList = [subList[0] for subList in entryList]
        maxTimeList = [subList[1] for subList in entryList]
        
        if minT != None:
            minTimeList.append(minT)
        if maxT != None:
            maxTimeList.append(maxT)

        try:
            self.minTimestamp = min(minTimeList)
            self.maxTimestamp = max(maxTimeList)
        except ValueError:
            raise TimelessTextgridTierException()

    def crop(self, cropStart, cropEnd, strictFlag, softFlag):
        '''
        Creates a new tier containing all entries that fit inside a new interval
        
        If strictFlag = True, only intervals wholly contained by the crop period
            will be kept
            
        If softFlag = True, the crop period will be stretched to the ends of intervals
            that are only partially contained by the crop period
            
        If both strictFlag and softFlag are set to false, partially contained tiers
            will be truncated in the output tier.
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
            elif softFlag and (intervalStart >= cropStart or intervalEnd <= cropEnd):
                matchedEntry = entry
            
            # If not strict, include partial tiers on the edges
            # -- regardless, record how much information was lost
            #        - for strict=True, the total time of the cut interval
            #        - for strict=False, the portion of the interval that lies
            #            outside the new interval

            # The current interval stradles the end of the new interval
            elif intervalStart >= cropStart and intervalEnd > cropEnd:
                cutTEnd = intervalEnd - cropEnd
                lastIntervalKeptProportion = (cropEnd - intervalStart) / (intervalEnd - intervalStart)

                if not strictFlag:
                    matchedEntry = (intervalStart, cropEnd, intervalLabel)
                    
                else:
                    cutTWithin += cropEnd - cropStart
            
            # The current interval stradles the start of the new interval
            elif intervalStart < cropStart and intervalEnd <= cropEnd:
                cutTStart = cropStart - intervalStart
                firstIntervalKeptProportion = (intervalEnd - cropStart) / (intervalEnd - intervalStart)
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
                        
            if matchedEntry != None:
                newEntryList.append(matchedEntry)

        if len(newEntryList) == 0:
            newEntryList.append( (0, cropEnd-cropStart, ""))

        # Create subtier
        subTier = IntervalTier(self.name, newEntryList, cropStart, cropEnd)
        return subTier, cutTStart, cutTWithin, cutTEnd, firstIntervalKeptProportion, lastIntervalKeptProportion
        
         
    def editTimestamps(self, startOffset, stopOffset, allowOvershoot=False):
        '''
        Modifies all timestamps by a constant amount
        
        Can modify the interval start independent of the interval end
        
        If allowOvershoot is True, an interval can go beyond the bounds
        of the textgrid 
        '''
        
        newEntryList = []
        for start, stop, label in self.entryList:
            
            newStart = startOffset+start            
            newStop = stopOffset + stop
            if not allowOvershoot:
                assert(newStart > self.minTimestamp)
                assert(newStop <= self.maxTimestamp)
            
            newEntryList.append( (newStart, newStop, label) )

        # Determine new min and max timestamps        
        newMin = min([entry[0] for entry in newEntryList])
        newMax = max([entry[1] for entry in newEntryList])
            
        if newMin > self.minTimestamp:
            newMin = self.minTimestamp
        
        if newMax < self.maxTimestamp:
            newMax = self.maxTimestamp
        
        return IntervalTier(self.name, newEntryList, newMin, newMax)
    
    
    def getEntries(self, start=None, stop=None, boundaryInclusive=False):
        
        if start == None:
            start = self.minTimestamp
        
        if stop == None:
            end = self.maxTimestamp
        
        returnList = []
        for entry in self.entryList:
            if intervalOverlapCheck(entry, (start, stop, ""),
                                    boundaryInclusive=boundaryInclusive):
                returnList.append(entry)
        
        return returnList
    
    
    def getDurationOfIntervals(self):
        return [float(subList[1]) - float(subList[0]) for subList in self.entryList]
    
    
    def getNonEntries(self, includeSilence):
        '''
        Returns the regions of the textgrid without labels
        
        This can include unlabeled segments and regions marked as silent.
        '''
        extractList = []
        entryList = self.getIntervals(not includeSilence)
        invertedEntryList = [(entryList[i][1], entryList[i+1][0], "") for i in xrange(len(entryList)-1)]
        
        if entryList[0][0] > 0:
            invertedEntryList.insert(0, (0, entryList[0][0], ""))
        
        if entryList[-1][1] < self.maxTimestamp:
            invertedEntryList.append((entryList[-1][1], self.maxTimestamp, ""))
            
        return invertedEntryList
    
    
    def insert(self, entry, warnFlag, collisionCode=None):
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
        startTime, endTime, label = entry
        
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
            matchList.sort() # By starting time
            
            newEntry = [min([mStart for mStart, mEnd, mLabel in matchList]), 
                        max([mEnd for mStart, mEnd, mLabel in matchList]),
                        "-".join([mLabel for mStart, mEnd, mLabel in matchList])]
            self.entryList.append(entry)
            
        else:
            raise TextgridCollisionException(self.name, entry, matchList)
            
        self.entryList.sort()
        
        if len(matchList) != 0 and warnFlag == True:
            print "Collision warning for %s with items %s of tier %s" % (str(entry),
                                                                       str(matchList),
                                                                       self.name)


    def manipulate(self, modFunc, filterFunc):
        '''
        
        '''
        return _manipulate(self, functools.partial(_manipulateFunc, self, modFunc, filterFunc))
    
    
    def morph(self, targetTier):
        '''
        Makes one interval tier look more like another
        '''
        return _manipulate(self, functools.partial(_morphFunc, self, targetTier))

    
    def newTier(self, name, entryList, minTimestamp=None, maxTimestamp=None):
        if minTimestamp == None:
            minTimestamp = self.minTimestamp
        if maxTimestamp == None:
            maxTimestamp = self.maxTimestamp
        return IntervalTier(name, entryList, minTimestamp, maxTimestamp)
    
        
class Textgrid():
    
    
    def __init__(self):
        self.tierNameList = [] # Preserves the order of the tiers
        self.tierDict = {}
    
        self.minTimestamp = None
        self.maxTimestamp = None
    
    
    def addTier(self, tier, tierIndex=None):
        
        if tierIndex == None:
            self.tierNameList.append(tier.name)
        else:
            self.tierNameList.insert(tierIndex, tier.name)
            
        assert(tier.name not in self.tierDict.keys())
        self.tierDict[tier.name] = tier
        
        minV = tier.minTimestamp
        if minV < self.minTimestamp or self.minTimestamp == None:
            self.minTimestamp = minV
        
        maxV = tier.maxTimestamp
        if maxV > self.maxTimestamp or self.maxTimestamp == None:
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
            
            elif onlyMatchingNames == False:
                retTG.addTier(tier)
        
        # Second add tiers that are only in the input tg
        if onlyMatchingNames == False:
            for name in tg.tierNameList:
                
                if name not in retTG.tierNameList:
                    tier = tier.offsetTimestamps(self.maxTimestamp, self.maxTimestamp)
                    retTG.addTier(tier)
        
        return retTG


    def crop(self, strictFlag, softFlag, startTime=None, endTime=None):
        
        if startTime == None:
            startTime = self.minTimestamp
            
        if startTime == None:
            endTime = self.maxTimestamp
            
        newTG = Textgrid()
        for tierName in self.tierNameList:
            tier = self.tierDict[tierName]
            if type(tier) == IntervalTier:
                newTier = tier.crop(startTime, endTime, strictFlag, softFlag)[0]
            elif type(tier) == PointTier:
                newTier = tier.crop(startTime, endTime)
            newTier.sort()
            
            newTG.addTier(newTier)
        
        return newTG


    def getContainedLabels(self, superTier):
        '''
        Returns a list of tiers that fall under each label in the given superTier
        
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
                            containedList.append( (tmpStart, tmpEnd, label) )
                        else:
                            break
                outputDict[subTier] = containedList
            returnList.append(outputDict)
            
        return returnList
    
    
    def getSubtextgrid(self, superTierName, qualifyingFunc, strictFlag):
        '''
        Returns intervals that are contained within qualifying superTier intervals
        
        For labeled regions in the super tier that pass the qualifyFunc,
        labeled intervals in the 
        
        If /strictFlag/ is True, only intervals wholly contained within the
        textgrid are included.  Otherwise, partially-contained intervals
        will also be included (but truncated to fit within the super tier).
        '''

        superTier = self.dataDict[superTierName]
        tierDataDict = {superTierName:superTier}
        for superEntry in superTier.entryList:
            if qualifyingFunc(superEntry):
                subTG = self.crop(strictFlag, False, superEntry[0], superEntry[1])
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
        
        if tierList == None:
            tierList = self.tierNameList
            
        if includeFunc == None:
            includeFunc = lambda entryList: not entryList[-1] == ''
           
        # Merge tiers
        superEntryList = []
        for tierName in tierList:
            tier = self.tierDict[tierName]
            superEntryList.extend(tier.entryList)
        
        superEntryList = [entry for entry in superEntryList if includeFunc(entry)]
            
        superEntryList.sort()
        
        # Combine overlapping intervals
        i = 0
        while i < len(superEntryList) - 1:
            currentEntry = superEntryList[i]
            nextEntry = superEntryList[i+1]
            
            if intervalOverlapCheck(currentEntry, nextEntry):
                currentStart, currentStop, currentLabel = superEntryList[i]
                nextStart, nextStop, nextLabel = superEntryList.pop(i+1)
                
                newStop = max([currentStop, nextStop])
                newLabel = "%s / %s" % (currentLabel, nextLabel)
                
                superEntryList[i] = (currentStart, newStop, newLabel)
                
            else:
                i += 1
            
        # Create the final textgrid
        tg = Textgrid() 
            
        # Preserve non-merged tiers
        if preserveOtherTiers == True:
            otherTierList = []
            for tierName in self.tierNameList:
                if tierName not in tierList:
                    tg.addTier(self.tierDict[tierName])

        # Add merged tier
        # (For this we can use any of the tiers involved in the merge to 
        # determine the tier type)
        tierName = "/".join(tierList)
        mergedTier = self.tierDict[tierList[0]].newTier(tierName, superEntryList)
        tg.addTier(mergedTier)
        
        return tg
    
    
    def offsetTimestamps(self, startOffset, stopOffset):
        
        tg = Textgrid()
        for tierName in self.tierNameList:
            tier = self.tierDict[tierName]
            tier = tier.offsetTimestamps(startOffset, stopOffset)
            
            tg.addTier(tier)
        
        return tg
    

    def renameTier(self, oldName, newName):
        oldTier = self.tierDict[oldName]
        tierIndex = self.tierNameList.index(oldName)
        self.removeTier(oldName)
        self.addTier(oldTier.newTier(newName, oldTier.entryList))


    def removeLabels(self, label, tierNameList=None):
        '''Remove labels from tiers'''
        
        # Remove from all tiers if no tiers are specified
        if tierNameList == None:
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
        
        # Fill in the blank spaces for interval tiers
        for name in self.tierNameList:
            tier = self.tierDict[name]
            if type(tier) == IntervalTier:
                self.tierDict[name] = _fillInBlanks(tier, 
                                                    startTime=self.minTimestamp,
                                                    endTime=self.maxTimestamp)
        
        self.sort()
        
        # Header
        outputTxt = ""
        outputTxt += 'File type = "ooTextFile short"\n'
        outputTxt += '"TextGrid"\n\n'
        outputTxt += "%s\n%s\n" % (self.minTimestamp, self.maxTimestamp)
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
    
    caseA = u"ooTextFile short" in data
    caseB = u"item" not in data   
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
        tierStart = float(header.split("xmin = ")[1].split("\n", 1)[0])
        tierEnd = float(header.split("xmax = ")[1].split("\n", 1)[0])
        tierName = tierName.strip()[1:-1]
        
        # Get the tier entry list
        tierEntryList = []
        labelI = 0
        if tierType == INTERVAL_TIER:
            while True:
                try:
                    timeStart, timeStartI = _fetchRow(tierData, "xmin = ", labelI)
                    timeEnd, timeEndI = _fetchRow(tierData, "xmax = ", timeStartI)
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


def _findAll(txt, subStr):
    
    indexList = []
    index = 0
    while True:
        try:
            index = txt.index(subStr, index)
        except ValueError:
            break
        indexList.append(int(index))
        index += 1
    
    return indexList


def _parseShortTextGrid(data):
    '''
    Reads a short textgrid file
    '''
    newTG = Textgrid()
    
    tierList = data.split('"IntervalTier"')[1:]
    
    intervalIndicies = [(i, True) for i in _findAll(data, '"IntervalTier"')]
    pointIndicies = [(i, False) for i in _findAll(data, '"TextTier"')]
    
    indexList = intervalIndicies + pointIndicies
    indexList.append((len(data), None)) # The 'end' of the file
    indexList.sort()
    
    tupleList = [(indexList[i][0], indexList[i+1][0], indexList[i][1]) 
                 for i in xrange(len(indexList) - 1)]
    
    for blockStartI, blockEndI, isInterval in tupleList:
        tierData = data[blockStartI:blockEndI]
        
        # First row contains the tier type, which we already know
        metaStartI = _fetchRow(tierData, '', 0)[1] 
        
        # Tier meta-information
        tierName, tierNameEndI = _fetchRow(tierData, '', metaStartI)
        tierStartTime, tierStartTimeI = _fetchRow(tierData, '', tierNameEndI)
        tierEndTime, tierEndTimeI = _fetchRow(tierData, '', tierStartTimeI)
        tierNumItems, startTimeI = _fetchRow(tierData, '', tierEndTimeI)
        
        tierStartTime = float(tierStartTime)
        tierEndTime = float(tierEndTime)
        
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
                
            newTG.addTier(IntervalTier(tierName, entryList, tierStartTime, tierEndTime))
            
        else:
            while True:
                try:
                    time, labelI = _fetchRow(tierData, '', startTimeI)
                    label, startTimeI = _fetchRow(tierData, '', labelI)
                except (ValueError, IndexError):
                    break
                label == label.strip()
                if label == "":
                    continue
                entryList.append((time, label))
                
            newTG.addTier(PointTier(tierName, entryList, tierStartTime, tierEndTime))

    return newTG


def _fetchRow(dataStr, searchStr, index):
    startIndex = dataStr.index(searchStr, index) + len(searchStr)
    endIndex = dataStr.index("\n", startIndex)
    
    word = dataStr[startIndex:endIndex]
    word = word.strip()
    if word[0] == '"' and word[-1] == '"':
        word = word[1:-1]
    word = word.strip()
    
    return word, endIndex + 1


def readPitchTier(path, fn):
    data = open(join(path, fn), "r").read()
    dataList = data.split("\n")
    
    pitchTierheader = dataList[:6]
    pitchDataList = dataList[6:]
    outputPitchDataList = [(float(pitchValue), float(time)) for time, pitchValue in zip(pitchDataList[::2], pitchDataList[1::2])]

    return pitchTierheader, outputPitchDataList


def writePitchTier(path, fn, pitchHeader, pitchDataList):
    pitchList = pitchHeader + pitchDataList
    
    pitchTxt = "\n".join(pitchList)
    
    open(join(path, fn), "w").write(pitchTxt)


