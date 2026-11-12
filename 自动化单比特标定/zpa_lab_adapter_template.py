"""Fill with your lab's REAL return schemas before hardware execution.
This template deliberately fails closed. No credentials, auto-connect or eval.
"""

class LabAdapter:
    def __init__(self,config):
        self.config=config
        self.datavault_root=config.get('datavault_root',r'D:\DataVault\LQHL.dir\test.dir')

    def get_context(self,bit):
        """Return (qobj, sq) from the current authorized experiment session."""
        raise NotImplementedError('Bind the current lab session and check qobj identity')

    def preflight(self,bit,qobj,settings,bias_values):
        """Verify hardware limits/units, current scan applicability, RF amps,
        frequency range and adapter readback/ramping/rollback support.
        Return only after checks:
        {'approved': True, 'bias_limits': [real_min,real_max],
         'frequency_unit':'GHz', 'bias_mapping_confirmed':True,
         't1_zpa_is_selected_bias':True}
        Confirm t1(zpa=...) really means this absolute bias, not a relative pulse.
        """
        raise NotImplementedError('Supply real instrument limits, do not assume [-3,3] is safe')

    def snapshot(self,qobj):
        """Save all mutable regs and live instrument state affected by update=True.
        Must be a detached snapshot, not a reference to live mutable registers.
        """
        raise NotImplementedError('Implement full rollback snapshot')

    def wait_for_bias(self,qobj,target):
        """After regs.bias_z assignment, apply/ramp hardware, settle and read back.
        Throw if not stable or bias is outside limits. Do not simply sleep.
        """
        raise NotImplementedError('Apply and verify physical bias')

    def latest_datavault_path(self):
        """Return the exact DataVault file created by the current call, if the
        live layer exposes it. The pipeline must not select by newest filename.
        In a polling implementation use `datavault_live.wait_for_result` with
        the call start timestamp and exact bit/kind, then return its path.
        """
        raise NotImplementedError('Bind current DataVault result handle')

    def maximum_visibility(self,iqraw_return,qobj):
        # Example implementation for the observed HDF5 schema:
        # from datavault_live import iqraw_visibility
        # return iqraw_visibility(iqraw_return)['Maximum Visibility']
        """Return numeric visibility fraction [0,1] from THIS iqraw call.
        DataVault IQraw stores four labelled I/Q streams; it does not contain a
        literal Visibility column. Implement the same visibility calculation
        used by the live iqraw routine, or return an explicit fitted metric.
        Never parse an unrelated/stale console line.
        """
        raise NotImplementedError('Map live IQraw result or implement validated visibility fit')

    def piamp_excited(self,piamp_return,qobj):
        """Return explicit bool from the current Rabi/piamp data.
        Define lab-approved oscillation contrast/fit quality/period support rule.
        A plot existing or piamp returning None is NOT evidence of excitation.
        """
        raise NotImplementedError('Define and implement excitation acceptance')

    def set_pi_half(self,sq,qobj):
        """Compatibility hook retained for custom setups. The default pipeline
        now uses sq.set_pi(qobj, gate='X/2') directly."""
        return sq.set_pi(qobj, gate='X/2')

    def final_metrics(self,qobj,returns):
        """Extract from actual current fits, not old regs/defaults:
        readout_fidelity: fraction [0,1], define avg(F0,F1) if appropriate;
        T1_us: from t1 fit; T2_us: from ramsey_df fit if it really fits decay;
        T2_type: e.g. 'Ramsey T2*'; qubit_frequency_GHz: final calibrated f01.
        Optional F0/F1, fit uncertainties and dataset IDs encouraged.
        If ramsey_df only determines detuning, a separate T2 experiment is needed
        and must be explicitly added/approved; never label detuning as T2.
        """
        raise NotImplementedError('Map current return values and explicit unit conversions')

    def safe_shutdown(self,qobj):
        """Stop drive/RF using the verified lab API. Also used after success."""
        raise NotImplementedError('Implement safe drive shutdown')

    def restore(self,qobj,snapshot):
        """On no working bias/error/interruption restore regs and hardware safely."""
        raise NotImplementedError('Implement verified safe rollback')


def create_adapter(config):return LabAdapter(config)
