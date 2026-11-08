// SPDX-License-Identifier: MIT OR GPL-2.0-only
/*
 * ASUS FX608JPR GPE14 keyboard wake workaround.
 * Unloading restores the original wake mask, not the capability flag.
 * No firmware, AML, IRQ handler, or runtime GPE enable changes.
 */
#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/acpi.h>
#include <linux/dmi.h>
#include <linux/module.h>

#define ASUS_WAKE_GPE 0x14

static bool previously_enabled;

static acpi_status restore_wake_mask(void)
{
	return acpi_set_gpe_wake_mask(NULL, ASUS_WAKE_GPE,
		previously_enabled ? ACPI_GPE_ENABLE : ACPI_GPE_DISABLE);
}

static int __init asus_gpe_wake_init(void)
{
	acpi_event_status before, after;
	acpi_status status;

	/* The GPE number was verified only on this exact model. */
	if (!dmi_match(DMI_SYS_VENDOR, "ASUSTeK COMPUTER INC.") ||
	    !dmi_match(DMI_PRODUCT_NAME,
		       "ASUS TUF Gaming F16 FX608JPR_FX608JPR")) {
		pr_err("unsupported machine; refusing to modify ACPI\n");
		return -ENODEV;
	}

	status = acpi_get_gpe_status(NULL, ASUS_WAKE_GPE, &before);
	if (ACPI_FAILURE(status)) {
		pr_err("cannot read GPE14 status: 0x%x\n", status);
		return -ENODEV;
	}
	previously_enabled = !!(before & ACPI_EVENT_FLAG_WAKE_ENABLED);
	pr_info("original status=0x%x wake_enabled=%d\n",
		before, previously_enabled);

	/* This capability flag remains in RAM until reboot. */
	status = acpi_mark_gpe_for_wake(NULL, ASUS_WAKE_GPE);
	if (ACPI_FAILURE(status)) {
		pr_err("cannot mark GPE14 wake-capable: 0x%x\n", status);
		return -EIO;
	}
	status = acpi_set_gpe_wake_mask(NULL, ASUS_WAKE_GPE, ACPI_GPE_ENABLE);
	if (ACPI_FAILURE(status)) {
		pr_err("cannot enable GPE14 wake mask: 0x%x\n", status);
		return -EIO;
	}
	status = acpi_get_gpe_status(NULL, ASUS_WAKE_GPE, &after);
	if (ACPI_FAILURE(status) || !(after & ACPI_EVENT_FLAG_WAKE_ENABLED)) {
		pr_err("GPE14 wake-mask verification failed\n");
		status = restore_wake_mask();
		if (ACPI_FAILURE(status))
			pr_err("cannot restore wake mask: 0x%x\n", status);
		return -EIO;
	}
	pr_info("GPE14 wake enabled, status=0x%x\n", after);
	return 0;
}

static void __exit asus_gpe_wake_exit(void)
{
	acpi_status status = restore_wake_mask();

	if (ACPI_FAILURE(status))
		pr_err("cannot restore wake mask: 0x%x; reboot to reset\n", status);
	else
		pr_info("restored original GPE14 wake mask\n");
}

module_init(asus_gpe_wake_init);
module_exit(asus_gpe_wake_exit);

MODULE_LICENSE("Dual MIT/GPL");
MODULE_DESCRIPTION("GPE14 keyboard wake workaround for ASUS FX608JPR");
MODULE_VERSION("1.0");
