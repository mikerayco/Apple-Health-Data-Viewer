#!/usr/bin/env python3
"""Generate deterministic, invented Apple Health test fixtures."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "synthetic" / "apple_health_export"

EXPORT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData [
<!-- HealthKit Export Version: 14 -->
<!ELEMENT HealthData (ExportDate,Me,(Record|Workout|ActivitySummary)*)>
<!ELEMENT ExportDate EMPTY>
<!ATTLIST ExportDate value CDATA #REQUIRED>
<!ELEMENT Me EMPTY>
<!ATTLIST Me
  HKCharacteristicTypeIdentifierDateOfBirth CDATA #REQUIRED
  HKCharacteristicTypeIdentifierBiologicalSex CDATA #REQUIRED
  HKCharacteristicTypeIdentifierBloodType CDATA #REQUIRED
  HKCharacteristicTypeIdentifierFitzpatrickSkinType CDATA #REQUIRED
  HKCharacteristicTypeIdentifierCardioFitnessMedicationsUse CDATA #REQUIRED>
<!ELEMENT Record (MetadataEntry|HeartRateVariabilityMetadataList)*>
<!ATTLIST Record type CDATA #REQUIRED unit CDATA #IMPLIED value CDATA #IMPLIED
  sourceName CDATA #REQUIRED sourceVersion CDATA #IMPLIED device CDATA #IMPLIED
  creationDate CDATA #IMPLIED startDate CDATA #REQUIRED endDate CDATA #REQUIRED>
<!ELEMENT MetadataEntry EMPTY>
<!ATTLIST MetadataEntry key CDATA #REQUIRED value CDATA #REQUIRED>
<!ELEMENT HeartRateVariabilityMetadataList (InstantaneousBeatsPerMinute*)>
<!ELEMENT InstantaneousBeatsPerMinute EMPTY>
<!ATTLIST InstantaneousBeatsPerMinute bpm CDATA #REQUIRED time CDATA #REQUIRED>
<!ELEMENT Workout (MetadataEntry|WorkoutEvent|WorkoutRoute|WorkoutStatistics)*>
<!ATTLIST Workout workoutActivityType CDATA #REQUIRED duration CDATA #IMPLIED
  durationUnit CDATA #IMPLIED sourceName CDATA #REQUIRED startDate CDATA #REQUIRED
  endDate CDATA #REQUIRED>
<!ELEMENT WorkoutEvent EMPTY>
<!ATTLIST WorkoutEvent type CDATA #REQUIRED date CDATA #REQUIRED>
<!ELEMENT WorkoutRoute (FileReference*)>
<!ATTLIST WorkoutRoute sourceName CDATA #REQUIRED startDate CDATA #REQUIRED endDate CDATA #REQUIRED>
<!ELEMENT FileReference EMPTY>
<!ATTLIST FileReference path CDATA #REQUIRED>
<!ELEMENT WorkoutStatistics EMPTY>
<!ATTLIST WorkoutStatistics type CDATA #REQUIRED startDate CDATA #REQUIRED endDate CDATA #REQUIRED
  average CDATA #IMPLIED minimum CDATA #IMPLIED maximum CDATA #IMPLIED sum CDATA #IMPLIED unit CDATA #IMPLIED>
<!ELEMENT ActivitySummary EMPTY>
<!ATTLIST ActivitySummary dateComponents CDATA #REQUIRED activeEnergyBurned CDATA #IMPLIED
  activeEnergyBurnedGoal CDATA #IMPLIED activeEnergyBurnedUnit CDATA #IMPLIED
  appleExerciseTime CDATA #IMPLIED appleExerciseTimeGoal CDATA #IMPLIED
  appleStandHours CDATA #IMPLIED appleStandHoursGoal CDATA #IMPLIED>
]>
<HealthData locale="en_US">
 <ExportDate value="2024-01-04 12:00:00 +0000"/>
 <Me HKCharacteristicTypeIdentifierDateOfBirth="1990-01-01"
     HKCharacteristicTypeIdentifierBiologicalSex="HKBiologicalSexNotSet"
     HKCharacteristicTypeIdentifierBloodType="HKBloodTypeNotSet"
     HKCharacteristicTypeIdentifierFitzpatrickSkinType="HKFitzpatrickSkinTypeNotSet"
     HKCharacteristicTypeIdentifierCardioFitnessMedicationsUse="None"/>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="Synthetic Watch" unit="count"
     creationDate="2024-01-02 09:05:00 +0000" startDate="2024-01-02 08:00:00 +0000"
     endDate="2024-01-02 09:00:00 +0000" value="1000">
  <MetadataEntry key="HKMetadataKeySyncIdentifier" value="synthetic-watch-steps-1"/>
 </Record>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="Synthetic Phone" unit="count"
     creationDate="2024-01-02 09:05:00 +0000" startDate="2024-01-02 08:00:00 +0000"
     endDate="2024-01-02 09:00:00 +0000" value="800"/>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="Synthetic Phone" unit="count"
     creationDate="2024-01-02 10:05:00 +0000" startDate="2024-01-02 09:00:00 +0000"
     endDate="2024-01-02 10:00:00 +0000" value="500"/>
 <Record type="HKQuantityTypeIdentifierRestingHeartRate" sourceName="Synthetic Watch" unit="count/min"
     startDate="2024-01-02 07:00:00 +0000" endDate="2024-01-02 07:00:00 +0000" value="60"/>
 <Record type="HKQuantityTypeIdentifierHeartRateVariabilitySDNN" sourceName="Synthetic Watch" unit="ms"
     startDate="2024-01-02 07:05:00 +0000" endDate="2024-01-02 07:05:00 +0000" value="50">
  <HeartRateVariabilityMetadataList>
   <InstantaneousBeatsPerMinute bpm="60" time="0.0"/>
   <InstantaneousBeatsPerMinute bpm="62" time="1.0"/>
  </HeartRateVariabilityMetadataList>
 </Record>
 <Record type="HKQuantityTypeIdentifierBodyMass" sourceName="Synthetic Scale" unit="kg"
     startDate="2024-01-02 07:10:00 +0000" endDate="2024-01-02 07:10:00 +0000" value="70"/>
 <Record type="HKQuantityTypeIdentifierBloodGlucose" sourceName="Synthetic Glucose Meter" unit="mg/dL"
     startDate="2024-01-02 07:15:00 +0000" endDate="2024-01-02 07:15:00 +0000" value="90">
  <MetadataEntry key="HKBloodGlucoseMealTime" value="1"/>
 </Record>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="Synthetic Watch" unit="count"
     creationDate="2024-01-02 09:05:00 +0000" startDate="2024-01-02 08:00:00 +0000"
     endDate="2024-01-02 09:00:00 +0000" value="1000">
  <MetadataEntry key="HKMetadataKeySyncIdentifier" value="synthetic-watch-steps-1"/>
 </Record>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Synthetic Watch"
     startDate="2024-01-01 23:00:00 +0000" endDate="2024-01-02 07:00:00 +0000"
     value="HKCategoryValueSleepAnalysisInBed"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Synthetic Watch"
     startDate="2024-01-01 23:30:00 +0000" endDate="2024-01-02 03:30:00 +0000"
     value="HKCategoryValueSleepAnalysisAsleepCore"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Synthetic Watch"
     startDate="2024-01-02 03:30:00 +0000" endDate="2024-01-02 05:00:00 +0000"
     value="HKCategoryValueSleepAnalysisAsleepDeep"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Synthetic Watch"
     startDate="2024-01-02 05:00:00 +0000" endDate="2024-01-02 06:30:00 +0000"
     value="HKCategoryValueSleepAnalysisAsleepREM"/>
 <Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="30" durationUnit="min"
     sourceName="Synthetic Watch" startDate="2024-01-02 08:00:00 +0000"
     endDate="2024-01-02 08:30:00 +0000">
  <WorkoutStatistics type="HKQuantityTypeIdentifierDistanceWalkingRunning"
      startDate="2024-01-02 08:00:00 +0000" endDate="2024-01-02 08:30:00 +0000"
      sum="5" unit="km"/>
  <WorkoutRoute sourceName="Synthetic Watch" startDate="2024-01-02 08:00:00 +0000"
      endDate="2024-01-02 08:30:00 +0000">
   <FileReference path="workout-routes/route_2024-01-02_080000.gpx"/>
  </WorkoutRoute>
 </Workout>
 <ActivitySummary dateComponents="2024-01-02" activeEnergyBurned="500"
     activeEnergyBurnedGoal="600" activeEnergyBurnedUnit="kcal"
     appleExerciseTime="30" appleExerciseTimeGoal="30"
     appleStandHours="10" appleStandHoursGoal="12"/>
</HealthData>
"""

ROUTE_GPX = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="Apple Health Data Viewer synthetic fixture"
     xmlns="http://www.topografix.com/GPX/1/1">
 <metadata><name>Synthetic running route</name></metadata>
 <trk><name>Synthetic running route</name><trkseg>
  <trkpt lat="0.0010" lon="0.0010"><ele>10.0</ele><time>2024-01-02T08:00:00Z</time></trkpt>
  <trkpt lat="0.0020" lon="0.0020"><ele>12.0</ele><time>2024-01-02T08:15:00Z</time></trkpt>
  <trkpt lat="0.0030" lon="0.0015"><ele>11.0</ele><time>2024-01-02T08:30:00Z</time></trkpt>
 </trkseg></trk>
</gpx>
"""

ECG_CSV = """Name,Synthetic Person
Date of Birth,1990-01-01
Recorded Date,2024-01-03 08:00:00 +0000
Classification,Sinus Rhythm
Symptoms,None
Software Version,1.0
Device,Synthetic Watch
Sample Rate,512 Hz
Lead,Lead I
Unit,µV
Sample,Amplitude
0,0
1,100
2,-50
3,25
"""

FILES = {
    Path("export.xml"): EXPORT_XML,
    Path("workout-routes/route_2024-01-02_080000.gpx"): ROUTE_GPX,
    Path("electrocardiograms/ecg_2024-01-03.csv"): ECG_CSV,
}


def check() -> list[str]:
    """Return descriptions of missing or stale generated files."""
    problems: list[str] = []
    for relative, expected in FILES.items():
        path = FIXTURE_ROOT / relative
        if not path.exists():
            problems.append(f"missing: {path.relative_to(ROOT)}")
        elif path.read_text(encoding="utf-8") != expected:
            problems.append(f"stale: {path.relative_to(ROOT)}")
    return problems


def generate() -> None:
    for relative, content in FILES.items():
        path = FIXTURE_ROOT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify fixtures without changing them")
    args = parser.parse_args()

    if args.check:
        problems = check()
        if problems:
            print("Synthetic fixtures need regeneration:", file=sys.stderr)
            for problem in problems:
                print(f"- {problem}", file=sys.stderr)
            return 1
        print(f"Synthetic fixtures are current ({len(FILES)} files).")
        return 0

    generate()
    print(f"Generated {len(FILES)} synthetic fixture files in {FIXTURE_ROOT.relative_to(ROOT)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
