/* SPDX-License-Identifier: MIT
 * ASUS FX608JPR BIOS 312 ONLY. Shared EC wake event, not a keyboard-only IRQ.
 * The original _GPE._L14 calls \_SB.PC00.LPCB.ECLV.
 * Add the missing wake declaration to the active lid, without replacing DSDT.
 * Zero advertises S0/s2idle only; it does not claim S3/S4 support.
 * May also enable lid/EC wake or cause spurious wakeups. Test on a desk.
 */
DefinitionBlock ("", "SSDT", 2, "LOCAL", "FX608WAK", 0x00000001)
{
    External (\_SB.PC00.LPCB.LID0, DeviceObj)
    Scope (\_SB.PC00.LPCB.LID0)
    {
        Name (_PRW, Package (0x02)
        {
            0x14,
            Zero
        })
    }
}
