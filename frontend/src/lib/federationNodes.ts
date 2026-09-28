export interface FederatedStateNode {
  id: string
  name: string
  code: string
  agency: string
  jurisdiction: string
  stationCount: number
  localSensorHours: number
  privacyProtocol: string
  aggregationWeight: number
  currentRound: number
  totalRounds: number
  syncStatus: 'synced' | 'aggregating' | 'queued'
  lastWeightHash: string
  focusArea: string
  color: string
}

export const STATE_FEDERATED_NODES: FederatedStateNode[] = [
  {
    id: 'node-in-pb-01',
    name: 'Punjab State Air Mesh',
    code: 'PPCB',
    agency: 'Punjab Pollution Control Board',
    jurisdiction: 'Punjab (Amritsar, Patiala, Ludhiana)',
    stationCount: 42,
    localSensorHours: 48200,
    privacyProtocol: 'Differential Privacy (ε=1.2) · Local Gradients Only',
    aggregationWeight: 0.28,
    currentRound: 4,
    totalRounds: 4,
    syncStatus: 'synced',
    lastWeightHash: '0x8f4c...3e1a',
    focusArea: 'Stubble burning, rural biomass & trans-boundary harvest plumes',
    color: '#eab308',
  },
  {
    id: 'node-in-hr-01',
    name: 'Haryana State Node',
    code: 'HSPCB',
    agency: 'Haryana State Pollution Control Board',
    jurisdiction: 'Haryana (Karnal, Panipat, Gurugram)',
    stationCount: 34,
    localSensorHours: 36500,
    privacyProtocol: 'Differential Privacy (ε=1.2, δ=1e-5)',
    aggregationWeight: 0.24,
    currentRound: 4,
    totalRounds: 4,
    syncStatus: 'synced',
    lastWeightHash: '0x3b9a...7c2d',
    focusArea: 'Agricultural upwind corridors & industrial NCR periphery',
    color: '#38bdf8',
  },
  {
    id: 'node-in-dl-01',
    name: 'Delhi Airshed Node',
    code: 'DPCC',
    agency: 'Delhi Pollution Control Committee',
    jurisdiction: 'NCT of Delhi (Anand Vihar, ITO, IGI Airport)',
    stationCount: 40,
    localSensorHours: 62800,
    privacyProtocol: 'Secure Multi-Party Aggregation (FedAvg v2.4)',
    aggregationWeight: 0.32,
    currentRound: 4,
    totalRounds: 4,
    syncStatus: 'synced',
    lastWeightHash: '0x9a2f...bb01',
    focusArea: 'Dense vehicular transit, road dust & urban smog inversion',
    color: '#a855f7',
  },
  {
    id: 'node-in-up-01',
    name: 'Uttar Pradesh Node',
    code: 'UPPCB',
    agency: 'UP Pollution Control Board',
    jurisdiction: 'Western UP (Noida, Ghaziabad, Kanpur, Meerut)',
    stationCount: 28,
    localSensorHours: 29100,
    privacyProtocol: 'Local Weight Divergence Validation (<0.04)',
    aggregationWeight: 0.16,
    currentRound: 4,
    totalRounds: 4,
    syncStatus: 'synced',
    lastWeightHash: '0x5e10...94fa',
    focusArea: 'Downwind Indo-Gangetic basin & industrial brick-kiln clusters',
    color: '#f97316',
  },
]
