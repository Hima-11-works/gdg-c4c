export type GrapStage = 'NONE' | 'STAGE_I' | 'STAGE_II' | 'STAGE_III' | 'STAGE_IV'

export interface GrapProtocol {
  stage: GrapStage
  stageNumber: number
  title: string
  aqiBand: string
  pm25Range: string
  badgeClass: string
  color: string
  mandatoryActions: string[]
  advisoryText: string
}

/**
 * Statutory Indian Graded Response Action Plan (GRAP) protocol lookup
 * as mandated by the Commission for Air Quality Management (CAQM).
 */
export function getGrapProtocol(pm25: number | null): GrapProtocol {
  const val = pm25 ?? 0
  if (val >= 350) {
    return {
      stage: 'STAGE_IV',
      stageNumber: 4,
      title: 'GRAP Stage IV (Severe+ / Air Emergency)',
      aqiBand: 'Severe+',
      pm25Range: 'PM2.5 > 350 µg/m³',
      badgeClass: 'grap-stage-4',
      color: '#ef4444',
      mandatoryActions: [
        'Ban non-electric/non-CNG commercial freight trucks entering urban corridor',
        'Mandatory halt on all Construction & Demolition activities',
        'Enforce 50% Work-From-Home for government and private offices',
        'Continuous deployment of high-capacity anti-smog guns on major arterials',
      ],
      advisoryText: 'Statutory emergency protocols in force under CAQM Section 12.',
    }
  }
  if (val >= 250) {
    return {
      stage: 'STAGE_III',
      stageNumber: 3,
      title: 'GRAP Stage III (Severe Air Quality)',
      aqiBand: 'Severe',
      pm25Range: 'PM2.5: 251–350 µg/m³',
      badgeClass: 'grap-stage-3',
      color: '#f97316',
      mandatoryActions: [
        'Halt all non-essential construction and demolition activities',
        'Stop operation of stone crushers, brick kilns, and hot mix plants',
        'Ban BS-III petrol and BS-IV diesel commercial light vehicles',
        'Intensify anti-smog water misting at verified hotspot corridors',
      ],
      advisoryText: 'Mandated emergency curbs active across the airshed.',
    }
  }
  if (val >= 120) {
    return {
      stage: 'STAGE_II',
      stageNumber: 2,
      title: 'GRAP Stage II (Very Poor Air Quality)',
      aqiBand: 'Very Poor',
      pm25Range: 'PM2.5: 121–250 µg/m³',
      badgeClass: 'grap-stage-2',
      color: '#eab308',
      mandatoryActions: [
        'Deploy anti-smog guns & mobile water misting at busy junctions',
        'Ban diesel generator sets except for healthcare/critical utilities',
        'Increase metro and electric public bus transit frequency',
        'Enforce strict vigil against biomass and solid waste combustion',
      ],
      advisoryText: 'Targeted administrative interventions required.',
    }
  }
  if (val >= 60) {
    return {
      stage: 'STAGE_I',
      stageNumber: 1,
      title: 'GRAP Stage I (Poor Air Quality)',
      aqiBand: 'Poor',
      pm25Range: 'PM2.5: 61–120 µg/m³',
      badgeClass: 'grap-stage-1',
      color: '#84cc16',
      mandatoryActions: [
        'Mechanized road vacuum sweeping and water sprinkling',
        'Strict anti-dust guidelines enforcement on construction sites',
        'Diversion of non-destined transit traffic away from city center',
      ],
      advisoryText: 'Precautionary containment measures in place.',
    }
  }
  return {
    stage: 'NONE',
    stageNumber: 0,
    title: 'GRAP Baseline (Moderate / Satisfactory)',
    aqiBand: 'Satisfactory',
    pm25Range: 'PM2.5 ≤ 60 µg/m³',
    badgeClass: 'grap-stage-none',
    color: '#22c55e',
    mandatoryActions: [
      'Standard ambient air quality surveillance',
      'Continuous CPCB / SPCB sensor data validation',
    ],
    advisoryText: 'Conditions within standard statutory parameters.',
  }
}
