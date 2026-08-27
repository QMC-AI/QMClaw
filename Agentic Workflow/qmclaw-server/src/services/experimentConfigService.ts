import fs from 'fs';
import path from 'path';

const CONFIG_DIR = path.join(process.cwd(), 'config');
const EXPERIMENT_CONFIGS_PATH = path.join(CONFIG_DIR, 'experiment_configs.json');

export interface ExperimentConfig {
  name: string;
  description: string;
  function: string;
  defaultPlotCommand: string;
}

export interface ExperimentConfigs {
  experiments: Record<string, ExperimentConfig>;
}

function ensureConfigDir(): void {
  if (!fs.existsSync(CONFIG_DIR)) {
    fs.mkdirSync(CONFIG_DIR, { recursive: true });
  }
}

export function loadExperimentConfigs(): ExperimentConfigs {
  ensureConfigDir();
  if (!fs.existsSync(EXPERIMENT_CONFIGS_PATH)) {
    // Return default configs if file doesn't exist
    return getDefaultConfigs();
  }
  try {
    const content = fs.readFileSync(EXPERIMENT_CONFIGS_PATH, 'utf-8');
    return JSON.parse(content);
  } catch (e) {
    console.error('Failed to load experiment configs:', e);
    return getDefaultConfigs();
  }
}

export function saveExperimentConfigs(configs: ExperimentConfigs): void {
  ensureConfigDir();
  fs.writeFileSync(EXPERIMENT_CONFIGS_PATH, JSON.stringify(configs, null, 2), 'utf-8');
}

export function getExperimentConfig(expType: string): ExperimentConfig | null {
  const configs = loadExperimentConfigs();
  return configs.experiments[expType] || null;
}

export function updateExperimentConfig(expType: string, config: Partial<ExperimentConfig>): boolean {
  const configs = loadExperimentConfigs();
  if (!configs.experiments[expType]) {
    return false;
  }
  configs.experiments[expType] = {
    ...configs.experiments[expType],
    ...config,
  };
  saveExperimentConfigs(configs);
  return true;
}

function getDefaultConfigs(): ExperimentConfigs {
  return {
    experiments: {
      spectroscopy: {
        name: "Spectroscopy",
        description: "VNA spectroscopy — broad frequency scan to find qubit resonance",
        function: "sq.spectroscopy",
        defaultPlotCommand: "plt.title('Spectroscopy')\nplt.xlabel('Frequency (Hz)')\nplt.ylabel('S21 (dB)')\nplt.grid(True)"
      },
      s21: {
        name: "Cavity S21",
        description: "Cavity S21 — narrowband frequency scan around cavity resonance",
        function: "sq.s21",
        defaultPlotCommand: "plt.title('Cavity S21')\nplt.xlabel('Frequency (Hz)')\nplt.ylabel('S21 (dB)')\nplt.grid(True)"
      },
      iqraw: {
        name: "IQ Raw",
        description: "Acquire raw I/Q data for qubit state discrimination",
        function: "sq.iqraw",
        defaultPlotCommand: "plt.title('IQ Raw Data')\nplt.xlabel('I')\nplt.ylabel('Q')\nplt.grid(True)\nplt.axis('equal')"
      },
      t1: {
        name: "T1 Relaxation",
        description: "Measure qubit relaxation time via variable delay pulse sequence",
        function: "sq.t1",
        defaultPlotCommand: "plt.title('T1 Relaxation')\nplt.xlabel('Delay (ns)')\nplt.ylabel('Population')\nplt.grid(True)"
      },
      ramsey: {
        name: "Ramsey",
        description: "Ramsey with detuning — measure T2* dephasing time",
        function: "sq.ramsey",
        defaultPlotCommand: "plt.title('Ramsey Interference')\nplt.xlabel('Delay (ns)')\nplt.ylabel('Population')\nplt.grid(True)"
      },
      piamp: {
        name: "Pi Pulse Amplitude",
        description: "Calibrate π-pulse amplitude for X gate via Rabi oscillation",
        function: "sq.piamp",
        defaultPlotCommand: "plt.title('Pi Pulse Calibration')\nplt.xlabel('Amplitude')\nplt.ylabel('Population')\nplt.grid(True)"
      },
      xeb: {
        name: "Cross-Entropy Benchmarking",
        description: "Measure single-qubit gate fidelity",
        function: "sq.xeb",
        defaultPlotCommand: "plt.title('Cross-Entropy Benchmarking')\nplt.xlabel('Cycles')\nplt.ylabel('Fidelity')\nplt.grid(True)"
      },
      s21_dis: {
        name: "S21 Dispersive Shift",
        description: "Measure cavity transmission shift vs qubit state",
        function: "sq.s21_dis",
        defaultPlotCommand: "plt.title('Dispersive Shift')\nplt.xlabel('Frequency (Hz)')\nplt.ylabel('S21 (dB)')\nplt.grid(True)"
      },
      allxy: {
        name: "AllXY",
        description: "Characterize all 21 gate error combinations",
        function: "sq.allxy",
        defaultPlotCommand: "plt.title('AllXY Characterization')\nplt.xlabel('Gate Pair')\nplt.ylabel('Fidelity')\nplt.grid(True)"
      },
      single_shot: {
        name: "Single-shot Fidelity",
        description: "Measure qubit readout fidelity in single-shot regime",
        function: "sq.single_shot",
        defaultPlotCommand: "plt.title('Single Shot Fidelity')\nplt.xlabel('I')\nplt.ylabel('Q')\nplt.grid(True)\nplt.axis('equal')"
      },
      pulsed_spec: {
        name: "Pulsed Spectroscopy",
        description: "Qubit spectroscopy with pump pulse for higher SNR",
        function: "sq.pulsed_spec",
        defaultPlotCommand: "plt.title('Pulsed Spectroscopy')\nplt.xlabel('Frequency (Hz)')\nplt.ylabel('Population')\nplt.grid(True)"
      },
      swap: {
        name: "SWAP",
        description: "Characterize SWAP gate for two-qubit operations",
        function: "sq.swap",
        defaultPlotCommand: "plt.title('SWAP Characterization')\nplt.xlabel('Duration (ns)')\nplt.ylabel('Fidelity')\nplt.grid(True)"
      },
      drag_calibrate: {
        name: "DRAG Calibration",
        description: "Optimize DRAG coefficient for leakage suppression",
        function: "sq.drag_calibrate",
        defaultPlotCommand: "plt.title('DRAG Calibration')\nplt.xlabel('DRAG Coefficient')\nplt.ylabel('Fidelity')\nplt.grid(True)"
      }
    }
  };
}
