# -*- coding: utf-8 -*-
"""
Test suite to exercise most CameraAssembler and board functions

Author: Jeremy Martin Hill (jerhill@llnl.gov)
Author: Matthew Dayton (matthew@hcmos.com)

Copyright (c) 2025, Lawrence Livermore National Security, LLC.  All rights reserved.
LLNL-CODE-838080

This work was produced at the Lawrence Livermore National Laboratory (LLNL) under
contract no. DE-AC52-07NA27344 (Contract 44) between the U.S. Department of Energy (DOE)
and Lawrence Livermore National Security, LLC (LLNS) for the operation of LLNL.
'nsCamera' is distributed under the terms of the MIT license. All new contributions must
be made under this license.

Version: 2.1.4 (May 2026) - Nova Added, colorized and improved messaging
"""

import argparse
import json
import logging
import time
from collections import OrderedDict

from nsCamera.CameraAssembler import CameraAssembler
from nsCamera.utils.misc import getEnter

"""
When run from the command line, testSuite accepts the following parameters:
    -b      name of board: 'nova_v1', 'llnl_v1', or 'llnl_v4'; default='llnl_v4'
    -c      name of comm: 'GigE' or 'RS422'; default='GigE'
    -s      name of sensor: 'icarus', 'icarus2', 'hyperion', or 'daedalus';
            default='icarus2'
    -p      specified port number; default=None
    -i      specified ip address; default=None
    -v      verbosity level; default=4
    --batch run automatically without interaction
    --hw    wait for hardware triggering
"""


USE_COLOR = True


class TermColor:
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BLUE = "\033[94m"
    RESET = "\033[0m"


def colorize(text, color):
    if not USE_COLOR:
        return str(text)
    return color + str(text) + TermColor.RESET


def pass_msg(text):
    return colorize(text, TermColor.GREEN)


def warn_msg(text):
    return colorize(text, TermColor.YELLOW)


def fail_msg(text):
    return colorize(text, TermColor.RED)


def info_msg(text):
    return colorize(text, TermColor.BLUE)


def print_pass(text):
    print(pass_msg(text))


def print_warn(text):
    print(warn_msg(text))


def print_fail(text):
    print(fail_msg(text))


def print_info(text):
    print(info_msg(text))


def testSuite(
    board, comm, sensor, portNum, ipAdd, interactive=True, swtrigger=True, verbose=4
):
    """
    Regression testing script to exercise cameraAssembler functions and camera features.
    Comment out entries in 'tests' list to skip component tests.

    Args:
        board: board name for cameraAssembler
        comm: comm name for cameraAssembler
        sensor: sensor name for cameraAssembler
        portNum: optional port number
        ipAdd: optional IP address, e.g. '192.168.1.100'
        interactive: if False, does not wait for user input and skips some tests
        swtrigger: if True, uses software triggering
        verbose: verbosity level
    """
    tests = [
        "HST acquisition",
        "SaveTiffs",
        "PlotFrames",
        "SplitFrames",
        "POT/DAC set & read",
        "SW Trigger",
        "Arm/Disarm",
        "HST setting",
        "Reinitialization",
        "PowerSave",
        "Timer",
        "Register R/W",
        "Register self-clear",
        "Status dump",
        "LED",
        "Manual Timing",
        "HFW",
        "ZDT",
        "Interlacing",
    ]

    print_info("-Initial setup-")
    ca = CameraAssembler(
        commname=comm,
        boardname=board,
        sensorname=sensor,
        verbose=verbose,
        port=portNum,
        ip=ipAdd,
    )

    ca.tests = tests
    ca.interactive = interactive
    ca.swtrigger = swtrigger

    if ca.boardname == "llnl_v1":
        ca.potsdacsconfigured = "STAT_POTSCONFIGURED"
    elif ca.boardname in ("llnl_v4", "nova_v1"):
        ca.potsdacsconfigured = "STAT_DACSCONFIGURED"
    else:
        logging.warning(
            "Unrecognized boardname '%s'; assuming STAT_DACSCONFIGURED status bit",
            ca.boardname,
        )
        print_warn(
            "Unrecognized boardname '{}'; assuming STAT_DACSCONFIGURED status bit".format(
                ca.boardname
            )
        )
        ca.potsdacsconfigured = "STAT_DACSCONFIGURED"

    statusVerify(ca, [("STAT_TIMERCOUNTERRESET", 1)])

    if "PowerSave" in tests:
        powersave(ca)

    if "Arm/Disarm" in tests:
        arm_disarm(ca)

    if "HST setting" in tests:
        hst_setting(ca)

    if "HST acquisition" in tests:
        hst_acquisition(ca)

    if "Reinitialization" in tests and interactive:
        reinitialization(ca)

    if ca.sensorname in ("icarus", "icarus2", "hyperion"):
        sensorregs = test_icarus(ca)
    elif ca.sensorname == "daedalus":
        sensorregs = test_daedalus(ca)
    else:
        print_warn("Invalid sensorname, assuming Daedalus")
        sensorregs = test_daedalus(ca)

    if ca.boardname == "llnl_v1":
        boardregs, boardselfclear, boardrestore = test_v1(ca)
    elif ca.boardname == "llnl_v4":
        boardregs, boardselfclear, boardrestore = test_v4(ca)
    elif ca.boardname == "nova_v1":
        boardregs, boardselfclear, boardrestore = test_nova_v1(ca)
    else:
        logging.warning(
            "Unrecognized boardname '%s'; using generic LLNL_v4 register tests",
            ca.boardname,
        )
        print_warn(
            "Unrecognized boardname '{}'; using generic LLNL_v4 register tests".format(
                ca.boardname
            )
        )
        boardregs, boardselfclear, boardrestore = test_v4(ca)

    print_info("\n\n-Testing miscellaneous board features-")

    if "Timer" in tests:
        timer(ca)

    print("Temperature sensor reading: " + str(ca.getTemp()))
    time.sleep(1)

    if "POT/DAC set & read" in tests:
        potdacsetread(ca)

    if "Register self-clear" in tests:
        register_selfclear(boardrestore, boardselfclear, ca)

    if "Register R/W" in tests:
        registerRW(boardregs, boardrestore, ca, sensorregs)

    if "Status dump" in tests:
        statusDump(ca)

    ca.closeDevice()
    time.sleep(1)
    logging.info("Done")
    print_pass("Done")


def statusVerify(caObject, checklist, context=None):
    """
    Verify status/subregister values.

    Args:
        caObject: CameraAssembler object
        checklist: list of tuples:
            (subregister_name, expected_value)
            or
            (subregister_name, expected_value, description)
        context: optional string describing the test context
    """
    errs = 0

    if context:
        print_info("Status check: " + str(context))

    for item in checklist:
        if len(item) == 2:
            stat, flag = item
            description = None
        elif len(item) == 3:
            stat, flag, description = item
        else:
            print_fail(
                "+Error: malformed status checklist item: " + repr(item)
            )
            errs += 1
            continue

        err, check = caObject.getSubregister(stat)

        if err:
            errs += 1
            msg = "+Error: unable to read status bit '{}': {}".format(stat, err)
            if description:
                msg += "\n        Check purpose: " + str(description)
            print_fail(msg)
            logging.warning(msg)
            continue

        actual = bool(int(check))
        expected = bool(flag)

        if actual is not expected:
            errs += 1

            msg = (
                "+Status mismatch: {stat}\n"
                "        Expected: {expected}\n"
                "        Actual:   {actual}"
            ).format(
                stat=stat,
                expected=expected,
                actual=actual,
            )

            if context:
                msg += "\n        Context:  " + str(context)

            if description:
                msg += "\n        Meaning:  " + str(description)

            print_fail(msg)
            logging.warning(
                "Status verify mismatch: %s is %s, expected %s. Context: %s. Meaning: %s",
                stat,
                actual,
                expected,
                context,
                description,
            )

    if not errs:
        if context:
            print_pass("+Status verify passed: " + str(context))
        else:
            print_pass("+Status verify passed")


def powersave(caObject):
    print_info("\n-Testing PowerSave mode-")

    caObject.setPowerSave(1)
    statusVerify(
        caObject,
        [
            (
                "POWERSAVE",
                1,
                "PowerSave bit should assert after setPowerSave(1).",
            ),
        ],
        context="enable PowerSave",
    )

    caObject.setPowerSave(0)
    statusVerify(
        caObject,
        [
            (
                "POWERSAVE",
                0,
                "PowerSave bit should deassert after setPowerSave(0).",
            ),
        ],
        context="disable PowerSave",
    )


def arm_disarm(ca):
    print_info("\n-Testing Arm-")

    ca.setTiming("A", (2, 2))
    ca.setTiming("B", (2, 2))
    ca.arm()
    time.sleep(1)

    statusVerify(
        ca,
        [
            ("STAT_ADCSCONFIGURED", 1),
            (ca.potsdacsconfigured, 1),
            ("STAT_COARSE", 0),
            ("STAT_FINE", 0),
            ("STAT_ARMED", 1),
        ],
    )

    time.sleep(1)
    print_info("\n-Testing Disarm-")
    ca.disarm()
    statusVerify(ca, [("STAT_ARMED", 0)])
    time.sleep(1)


def hst_setting(caObject):
    print_info("\n-Testing high speed timing control-")
    errtemp = 0

    caObject.setTiming("A", (38, 2), 0)
    caObject.setTiming("B", (1, 2), 27)
    time.sleep(0.1)  # allow HST configuration to complete before checking status

    statusVerify(
    caObject,
    [
        (
            "STAT_HSTCONFIGDONE",
            1,
            "High-speed timing configuration should be complete after applying HST settings.",
        ),
        (
            "MANSHUT_MODE",
            0,
            "Manual shutter mode should be disabled when using high-speed timing mode.",
        ),
    ],
    context="high-speed timing configuration",
    )
    time.sleep(1)

    actual_a = caObject.getTiming("A")
    actual_b = caObject.getTiming("B")

    expected_a = ("A", 38, 2, 0)
    expected_b = ("B", 1, 2, 27)

    if expected_a != actual_a:
        errtemp = 1
        print_fail(
            "HST timing mismatch A: expected {}, got {}".format(
                expected_a, actual_a
            )
        )

    if expected_b != actual_b:
        errtemp = 1
        print_fail(
            "HST timing mismatch B: expected {}, got {}".format(
                expected_b, actual_b
            )
        )

    if errtemp:
        print_fail("Error in setting high speed timing")
        errtemp = 0

    caObject.setTiming("A", (5, 2), 3)
    caObject.setTiming("B", (3, 4), 1)

    if ("A", 5, 2, 3) != caObject.getTiming("A"):
        errtemp = 1
    if ("B", 3, 4, 1) != caObject.getTiming("B"):
        errtemp = 1

    if errtemp:
        print_fail("Error in setting high speed timing")

    caObject.setTiming("B", (10, 10), 0)
    time.sleep(1)

    print_warn(
        "The next message should include a warning message regarding timing sequence. Requested TM cant fit nicely:"
    )
    caObject.setTiming("A", (9, 8), 1)
    time.sleep(1)

    print_warn(
        "The next message should include an error message regarding timing sequence. Overflowed 40bit register:"
    )
    caObject.setTiming("A", (15, 15), 15)
 
    time.sleep(1)


def hst_acquisition(caObject):
    print_info("\n-Testing HST acquisition-")
    caObject.setTiming("B", (10, 10), 0)
    time.sleep(1)
    checkArmStatus(caObject)
    armBoard(caObject)
    frames, datalen, data_err = caObject.readoff(waitOnSRAM=True)

    if data_err:
        print_fail("+Error in acquisition!")
    else:
        print_pass("+No error in acquisition reported")

    plottiffs(caObject, frames, label="hst_test")

    if "SplitFrames" in caObject.tests:
        print_info("\n-Testing split-frame (two column) feature-")
        armBoard(caObject)
        frames, datalen, data_err = caObject.readoff(waitOnSRAM=True, columns=2)

        if data_err:
            print_fail("+Error in split-frame acquisition!")
        else:
            print_pass("+No error in split-frame acquisition reported")

        plottiffs(caObject, frames, label="splitframe_test")

    if not caObject.swtrigger and "SW Trigger" in caObject.tests:
        print_info("\n-Testing HST acquisition with software trigger-")
        checkArmStatus(caObject)

        caObject.arm("Software")
        statusVerify(caObject, [("STAT_COARSE", 1), ("STAT_FINE", 1)])

        frames, datalen, data_err = caObject.readoff(waitOnSRAM=True)

        print("Data length: " + str(datalen) + " bytes")

        if data_err:
            print_fail("+Error in acquisition!")
        else:
            print_pass("+No error in acquisition reported")

        plottiffs(caObject, frames, label="SWtrig_test")


def checkArmStatus(caObject):
    caObject.arm()
    time.sleep(1)
    caObject.reportStatus()

    statusVerify(
        caObject,
        [
            (
                "STAT_ADCSCONFIGURED",
                1,
                "ADC configuration should be complete before the camera is considered armed.",
            ),
            (
                caObject.potsdacsconfigured,
                1,
                "DAC/pot configuration should be complete before image acquisition.",
            ),
            (
                "STAT_HSTCONFIGDONE",
                1,
                "High-speed timing configuration should be complete after arming.",
            ),
            (
                "MANSHUT_MODE",
                0,
                "Manual shutter mode should be disabled for HST acquisition.",
            ),
            (
                "STAT_COARSE",
                0,
                "Coarse trigger should not already be detected before the acquisition trigger.",
            ),
            (
                "STAT_FINE",
                0,
                "Fine trigger should not already be detected before the acquisition trigger.",
            ),
        ],
        context="armed pre-acquisition status",
    )


def armBoard(caObject):
    if caObject.swtrigger:
        print_info("Software trigger selected")
    else:
        print_info("Hardware trigger selected")
    if caObject.swtrigger:
        caObject.arm("Software")
        time.sleep(0.05)  # allow trigger to be processed before reading status
        statusVerify(caObject, [("STAT_COARSE", 1), ("STAT_FINE", 1)])
    else:
        if caObject.interactive:
            caObject.arm()
            getEnter(
                "> Please initiate hardware trigger, then press ENTER to "
                "continue. <\n "
            )


def plottiffs(caObject, frames, label):
    if caObject.interactive:
        if "PlotFrames" in caObject.tests:
            print_info(
                "\nPlots of the acquired frames are being displayed. Please "
                "inspect to verify proper acquisition. "
                "\n> Close plots to continue <"
            )
            caObject.plotFrames(frames)

        if "SaveTiffs" in caObject.tests:
            caObject.saveTiffs(frames, filename=label)
            getEnter(
                "Tiff files of the acquired frames have been saved. Please "
                "inspect to verify correct saves. "
                "\n> Press ENTER to continue. <\n"
            )
    else:
        if "SaveTiffs" in caObject.tests:
            caObject.saveTiffs(frames, filename=label)
            print_pass("Tiffs from " + label + " have been saved")


def reinitialization(caObject):
    time.sleep(1)
    caObject.setTiming("A", (2, 3), 4)
    caObject.setTiming("B", (5, 3), 1)
    time.sleep(1)

    getEnter(
        "\n-Testing reinitialization with high speed timing-\n>Please power-cycle "
        "the board, then press ENTER to continue <"
    )

    if caObject.powerCheck():
        print_pass("\n+Loss of power WAS NOT detected")
    else:
        print_warn("\n+Loss of power WAS detected")

    time.sleep(1)
    caObject.reinitialize()
    statusVerify(caObject, [("STAT_TIMERCOUNTERRESET", 1)])

    if ("A", 2, 3, 4) != caObject.getTiming("A") or (
        "B",
        5,
        3,
        1,
    ) != caObject.getTiming("B"):
        print_fail("+High speed timing WAS NOT restored properly after reinitialization")
    else:
        print_pass("+High speed timing WAS restored properly after reinitialization")


def test_nova_v1(caObject):
    print_info("\n# Nova_v1 board-specific checks")

    if "POT/DAC set & read" in caObject.tests:
        print_info("\n-Nova DAC check-")

        # Speed up housekeeping ADC poll period to 100 ms (5 x 20 ms/tick) so
        # monitor readings reflect DAC changes within the per-step sleep below.
        # nova_restore resets ADC_PPER to the 1-second default at end of registerRW.
        caObject.board.setPPER(5)
        time.sleep(0.15)  # let new poll period take effect

        dac_monitor_map = OrderedDict(
            {
                "DACA": "MON_CH10",
                "DACB": "MON_CH16",
                "DACC": "MON_CH14",
                "DACD": "MON_CH11",
                "DACE": "MON_CH5",
                "DACF": "MON_CH13",
                "DACG": "MON_CH6",
                "DACH": "MON_CH8",
            }
        )

        for dacname, monname in dac_monitor_map.items():
            print("Testing " + dacname + " -> " + monname)
            temperr = 0

            # Keep this conservative. Some Nova bias nodes may not want full-range
            # sweeps during bring-up.
            for desired in [0.0, 0.5, 1.0, 1.5, 2.0]:
                minvolt = 0.05

                caObject.setPotV(dacname, desired, tune=False)
                time.sleep(0.25)  # > 2 poll cycles at 100 ms/cycle
                actual = caObject.getMonV(monname)
                delta_mv = 1000 * abs(actual - desired)

                msg = "{0:.2f} : actual = {1:.5f} ; delta = {2:.2f} mV".format(
                    desired, actual, delta_mv
                )

                if abs(desired - actual) > minvolt and bool(desired):
                    print_fail(msg)
                    temperr = 1
                else:
                    print_pass(msg)

            if not temperr:
                print_pass("+" + dacname + " monitor tracks expected voltage")
            else:
                print_warn("+" + dacname + " monitor check completed with warnings")

    nova_regs = OrderedDict(
        {
            "ADC_RESET": "0000000F",
            "DAC_REG_A_AND_B": "FFFFFFFF",
            "DAC_REG_C_AND_D": "FFFFFFFF",
            "DAC_REG_E_AND_F": "FFFFFFFF",
            "DAC_REG_G_AND_H": "FFFFFFFF",
            "SUSPEND_TIME": "FFFFFFFF",
            "DELAY_READOFF": "FFFFFFFF",
            "ADC1_CONFIG_DATA": "FFFFFFFF",
            "ADC2_CONFIG_DATA": "FFFFFFFF",
            "ADC3_CONFIG_DATA": "FFFFFFFF",
            "ADC4_CONFIG_DATA": "FFFFFFFF",
            "ADC_PPER": "000000FF",
        }
    )

    nova_selfclear = OrderedDict(
        {
            "DAC_CTL": "00000001",
            "SW_COARSE_CONTROL": "FFFFFFFF",
        }
    )

    nova_restore = [
        ("ADC_PPER", "00000032"),  # 1000 ms (1 s) default housekeeping poll period (50 x 20 ms/tick)
        ("ADC_RESET", "00000000"),
        ("ADC1_CONFIG_DATA", "81A801FF"),
        ("ADC2_CONFIG_DATA", "81A801FF"),
        ("ADC3_CONFIG_DATA", "81A801FF"),
        ("ADC4_CONFIG_DATA", "81A801FF"),
    ]

    return nova_regs, nova_selfclear, nova_restore


def test_icarus(caObject):
    print_info("\n# Icarus sensor-specific checks")

    if "Manual Timing" in caObject.tests:
        icarusManual(caObject)

    icarusregs = OrderedDict(
        {
            "VRESET_WAIT_TIME": "7FFFFFFF",
            "ICARUS_VER_SEL": "00000001",
            "MANUAL_SHUTTERS_MODE": "00000001",
            "W0_INTEGRATION": "03FFFFFF",
            "W0_INTERFRAME": "03FFFFFF",
            "W1_INTEGRATION": "03FFFFFF",
            "W1_INTERFRAME": "03FFFFFF",
            "W2_INTEGRATION": "03FFFFFF",
            "W2_INTERFRAME": "03FFFFFF",
            "W3_INTEGRATION": "03FFFFFF",
            "W0_INTEGRATION_B": "03FFFFFF",
            "W0_INTERFRAME_B": "03FFFFFF",
            "W1_INTEGRATION_B": "03FFFFFF",
            "W1_INTERFRAME_B": "03FFFFFF",
            "W2_INTEGRATION_B": "03FFFFFF",
            "W2_INTERFRAME_B": "03FFFFFF",
            "W3_INTEGRATION_B": "03FFFFFF",
            "MISC_SENSOR_CTL": "000001FF",
            # "TIME_ROW_DCD": "FFFFFFFF",
        }
    )

    if caObject.sensorname == "icarus":
        icarusregs["VRESET_HIGH_VALUE"] = "000000FF"

    return icarusregs


def icarusManual(caObject):
    print_info("\n-Testing manual shutter control-")

    caObject.setManualShutters(
        timing=[
            (100, 100, 100, 100, 100, 100, 100),
            (100, 100, 100, 100, 100, 100, 100),
        ]
    )

    statusVerify(
        caObject,
        [
            (
                "MANSHUT_MODE",
                1,
                "Manual shutter mode should be enabled after setManualShutters().",
            ),
        ],
        context="manual shutter setup",
    )

    time.sleep(1)

    caObject.setManualShutters(timing=[(100, 100, 100, 100, 100, 100, 100)])
    caObject.setManualShutters(
        timing=[
            (10.5, 100, 100, 100, 100, 100, 100),
            (100, 100, 100, 100, 100, 100, 100),
        ]
    )

    time.sleep(1)
    print_info("\n-Testing manual shutter acquisition-")

    if caObject.swtrigger:
        print_info("Using software trigger")
        statusVerify(
            caObject,
            [
                ("STAT_ADCSCONFIGURED", 1),
                (caObject.potsdacsconfigured, 1),
                ("STAT_HSTCONFIGDONE", 1),
                ("MANSHUT_MODE", 1),
            ],
        )
        armBoard(caObject)
    else:
        caObject.arm()
        if caObject.interactive:
            getEnter(
                "> Please initiate hardware trigger, then press ENTER to "
                "continue. <\n "
            )
        else:
            print_info("Waiting for hardware trigger")

    frames, datalen, data_err = caObject.readoff(waitOnSRAM=True)
    print("Data length: " + str(datalen))

    if data_err:
        print_fail("+Error in acquisition!")
    else:
        print_pass("+No error in acquisition reported")
        plottiffs(caObject, frames, "msc_test")

    if "Reinitialization" in caObject.tests and caObject.interactive:
        caObject.setManualShutters(
            timing=[
                (25, 50, 75, 100, 125, 150, 175),
                (175, 150, 125, 100, 75, 50, 25),
            ]
        )

        getEnter(
            "\n-Testing reinitialization with manual shutter control-\n> "
            "Please power-cycle the board, then press ENTER to continue <"
        )

        time.sleep(1)

        if caObject.powerCheck():
            print_pass("\n+Loss of power WAS NOT detected")
        else:
            print_warn("\n+Loss of power WAS detected")

        time.sleep(1)
        caObject.reinitialize()
        statusVerify(caObject, [("STAT_TIMERCOUNTERRESET", 1)])

        if caObject.sensor.getManualTiming() != [
            [25, 50, 75, 100, 125, 150, 175],
            [175, 150, 125, 100, 75, 50, 25],
        ]:
            print_fail("+Manual timing WAS NOT restored properly after reinitialization")
        else:
            print_pass("+Manual timing WAS restored properly after reinitialization")


def test_daedalus(caObject):
    print_info("\n# Daedalus sensor-specific checks")

    if "HFW" in caObject.tests:
        hfwTest(caObject)
    if "ZDT" in caObject.tests:
        zdtTest(caObject)
    if "Interlacing" in caObject.tests:
        interlaceTest(caObject)

    daedalusregs = OrderedDict(
        {
            "HSTALLWEN_WAIT_TIME": "7FFFFFFF",
            "VRESET_HIGH_VALUE": "0000FFFF",
            "FRAME_ORDER_SEL": "00000007",
            "HST_TRIGGER_DELAY_DATA_LO": "FFFFFFFF",
            "HST_TRIGGER_DELAY_DATA_HI": "000000FF",
            "HST_PHI_DELAY_DATA": "3FF003FF",
            "HST_COUNT_TRIG": "00000001",
            "HST_DELAY_EN": "00000001",
            "RSL_HFW_MODE_EN": "00000001",
            "RSL_ZDT_MODE_B_EN": "00000001",
            "RSL_ZDT_MODE_A_EN": "00000001",
            "BGTRIMA": "00000007",
            "BGTRIMB": "0000000F",
            "COLUMN_TEST_EN": "00000001",
            "RSL_CONFIG_DATA_B0": "FFFFFFFF",
            "RSL_CONFIG_DATA_B1": "FFFFFFFF",
            "RSL_CONFIG_DATA_B2": "FFFFFFFF",
            "RSL_CONFIG_DATA_B3": "FFFFFFFF",
            "RSL_CONFIG_DATA_B4": "FFFFFFFF",
            "RSL_CONFIG_DATA_B5": "FFFFFFFF",
            "RSL_CONFIG_DATA_B6": "FFFFFFFF",
            "RSL_CONFIG_DATA_B7": "FFFFFFFF",
            "RSL_CONFIG_DATA_B8": "FFFFFFFF",
            "RSL_CONFIG_DATA_B9": "FFFFFFFF",
            "RSL_CONFIG_DATA_B10": "FFFFFFFF",
            "RSL_CONFIG_DATA_B11": "FFFFFFFF",
            "RSL_CONFIG_DATA_B12": "FFFFFFFF",
            "RSL_CONFIG_DATA_B13": "FFFFFFFF",
            "RSL_CONFIG_DATA_B14": "FFFFFFFF",
            "RSL_CONFIG_DATA_B15": "FFFFFFFF",
            "RSL_CONFIG_DATA_B16": "FFFFFFFF",
            "RSL_CONFIG_DATA_B17": "FFFFFFFF",
            "RSL_CONFIG_DATA_B18": "FFFFFFFF",
            "RSL_CONFIG_DATA_B19": "FFFFFFFF",
            "RSL_CONFIG_DATA_B20": "FFFFFFFF",
            "RSL_CONFIG_DATA_B21": "FFFFFFFF",
            "RSL_CONFIG_DATA_B22": "FFFFFFFF",
            "RSL_CONFIG_DATA_B23": "FFFFFFFF",
            "RSL_CONFIG_DATA_B24": "FFFFFFFF",
            "RSL_CONFIG_DATA_B25": "FFFFFFFF",
            "RSL_CONFIG_DATA_B26": "FFFFFFFF",
            "RSL_CONFIG_DATA_B27": "FFFFFFFF",
            "RSL_CONFIG_DATA_B28": "FFFFFFFF",
            "RSL_CONFIG_DATA_B29": "FFFFFFFF",
            "RSL_CONFIG_DATA_B30": "FFFFFFFF",
            "RSL_CONFIG_DATA_B31": "FFFFFFFF",
            "RSL_CONFIG_DATA_A0": "FFFFFFFF",
            "RSL_CONFIG_DATA_A1": "FFFFFFFF",
            "RSL_CONFIG_DATA_A2": "FFFFFFFF",
            "RSL_CONFIG_DATA_A3": "FFFFFFFF",
            "RSL_CONFIG_DATA_A4": "FFFFFFFF",
            "RSL_CONFIG_DATA_A5": "FFFFFFFF",
            "RSL_CONFIG_DATA_A6": "FFFFFFFF",
            "RSL_CONFIG_DATA_A7": "FFFFFFFF",
            "RSL_CONFIG_DATA_A8": "FFFFFFFF",
            "RSL_CONFIG_DATA_A9": "FFFFFFFF",
            "RSL_CONFIG_DATA_A10": "FFFFFFFF",
            "RSL_CONFIG_DATA_A11": "FFFFFFFF",
            "RSL_CONFIG_DATA_A12": "FFFFFFFF",
            "RSL_CONFIG_DATA_A13": "FFFFFFFF",
            "RSL_CONFIG_DATA_A14": "FFFFFFFF",
            "RSL_CONFIG_DATA_A15": "FFFFFFFF",
            "RSL_CONFIG_DATA_A16": "FFFFFFFF",
            "RSL_CONFIG_DATA_A17": "FFFFFFFF",
            "RSL_CONFIG_DATA_A18": "FFFFFFFF",
            "RSL_CONFIG_DATA_A19": "FFFFFFFF",
            "RSL_CONFIG_DATA_A20": "FFFFFFFF",
            "RSL_CONFIG_DATA_A21": "FFFFFFFF",
            "RSL_CONFIG_DATA_A22": "FFFFFFFF",
            "RSL_CONFIG_DATA_A23": "FFFFFFFF",
            "RSL_CONFIG_DATA_A24": "FFFFFFFF",
            "RSL_CONFIG_DATA_A25": "FFFFFFFF",
            "RSL_CONFIG_DATA_A26": "FFFFFFFF",
            "RSL_CONFIG_DATA_A27": "FFFFFFFF",
            "RSL_CONFIG_DATA_A28": "FFFFFFFF",
            "RSL_CONFIG_DATA_A29": "FFFFFFFF",
            "RSL_CONFIG_DATA_A30": "FFFFFFFF",
            "RSL_CONFIG_DATA_A31": "FFFFFFFF",
        }
    )

    return daedalusregs


def hfwTest(caObject):
    print_info("\n-Testing High Full Well mode-")
    caObject.setHighFullWell(True)
    armBoard(caObject)
    frames, datalen, data_err = caObject.readoff(waitOnSRAM=True)

    if data_err:
        print_fail("+Error in HFW acquisition!")
    else:
        print_pass("+No error in HFW acquisition reported")

    plottiffs(caObject, frames, label="hfw_test")
    caObject.setHighFullWell(False)


def zdtTest(caObject):
    print_info("\n-Testing Zero Dead Time mode-")
    caObject.setZeroDeadTime(True)
    armBoard(caObject)
    frames, datalen, data_err = caObject.readoff(waitOnSRAM=True)

    if data_err:
        print_fail("+Error in ZDT acquisition!")
    else:
        print_pass("+No error in ZDT acquisition reported")

    plottiffs(caObject, frames, label="zdt_test")
    caObject.setZeroDeadTime(False)


def interlaceTest(caObject):
    print_info("\n-Testing Interlacing (factor = 2)-")
    caObject.setInterlacing(2)
    armBoard(caObject)
    frames, datalen, data_err = caObject.readoff(waitOnSRAM=True)

    if data_err:
        print_fail("+Error in interlace acquisition!")
    else:
        print_pass("+No error in interlace acquisition reported")

    plottiffs(caObject, frames, label="interlace_test")
    caObject.setInterlacing(0)


def timer(caObject):
    print_info("Checking on-board timer reset")
    caObject.resetTimer()
    ztime = caObject.getTimer()

    if not ztime:
        print_pass("+Timer reset check successful")
    else:
        print_fail("+Timer reset failed, timer reads " + str(ztime))

    statusVerify(caObject, [("STAT_TIMERCOUNTERRESET", 1)])


def test_v1(caObject):
    print_info("\n# LLNL_v1 board-specific checks")

    if "LED" in caObject.tests:
        print_info("\nRolling LEDs")
        caObject.enableLED(0)
        caObject.enableLED(1)

        for i in range(1, 5):
            for j in range(1, 9):
                caObject.setLED(j, 1)
                time.sleep(0.05 * i)
                caObject.setLED(j, 0)

    if "POT/DAC set & read" in caObject.tests:
        time.sleep(1)
        print_info("\n-Pot check-")

        for i in [2, 3, 4, 6, 8]:
            potname = "POT" + str(i)
            monname = "MON_CH" + str(i)
            print("Testing " + potname)
            temperr = 0

            for j in range(7):
                desired = j * 0.5
                potobj = getattr(caObject.board, potname)
                minvolt = potobj.resolution
                caObject.setPotV(potname, desired, tune=True)
                actual = caObject.getMonV(monname)
                delta_mv = 1000 * abs(actual - desired)

                msg = "{0:.2f} : actual = {1:.5f} ; delta = {2:.2f} mV".format(
                    float(desired), actual, delta_mv
                )

                if abs(desired - actual) > minvolt and bool(desired):
                    print_fail(msg)
                    temperr = 1
                else:
                    print_pass(msg)

            if not temperr:
                print_pass("+" + potname + " tunes properly")

    v1regs = OrderedDict(
        {
            "ADC_RESET": "0000001F",
            "ADC5_CONFIG_DATA": "FFFFFFFF",
            "POT_REG4_TO_1": "FFFFFFFF",
            "POT_REG8_TO_5": "FFFFFFFF",
            "POT_REG12_TO_9": "FFFFFFFF",
            "POT_REG13": "000000FF",
            "ADC5_PPER": "0FFFFFFF",
            "LED_GP": "000000FF",
            "ADC_STANDBY": "0000001F",
            "TEMP_SENSE_PPER": "0FFFFFFF",
            "SENSOR_VOLT_CTL": "00000001",
        }
    )

    v1selfclear = OrderedDict(
        {
            "POT_CTL": "00000001",
        }
    )

    v1restore = [
        ("ADC5_PPER", "001E8480"),
        ("ADC_RESET", "00000000"),
        ("ADC5_CONFIG_DATA", "81A883FF"),
        ("ADC_CTL", "00000010"),
    ]

    return v1regs, v1selfclear, v1restore


def test_v4(caObject):
    print_info("\n# LLNL_v4 board-specific checks")

    if "POT/DAC set & read" in caObject.tests:
        print_info("\n-DAC check-")

        if caObject.sensorname == "daedalus":
            daclist = ["C", "E", "F", "G", "H"]
        else:
            daclist = ["A", "B", "C", "D", "E", "F", "G", "H"]

        for i in daclist:
            dacname = "DAC" + i
            monname = "MON_CH" + i
            print("Testing " + dacname)
            temperr = 0

            for j in range(7):
                desired = j * 0.5
                minvolt = 0.01

                caObject.setPotV(dacname, desired, tune=True)
                actual = caObject.getMonV(monname)
                delta_mv = 1000 * abs(actual - desired)

                msg = "{0:.2f} : actual = {1:.5f} ; delta = {2:.2f} mV".format(
                    float(desired), actual, delta_mv
                )

                if abs(desired - actual) > minvolt and bool(desired):
                    print_fail(msg)
                    temperr = 1
                else:
                    print_pass(msg)

            if not temperr:
                print_pass("+" + dacname + " tunes properly")

    v4regs = OrderedDict(
        {
            "ADC_RESET": "0000000F",
            "DAC_REG_A_AND_B": "FFFFFFFF",
            "DAC_REG_C_AND_D": "FFFFFFFF",
            "DAC_REG_E_AND_F": "FFFFFFFF",
            "DAC_REG_G_AND_H": "FFFFFFFF",
            "SUSPEND_TIME": "FFFFFFFF",
            "DELAY_READOFF": "FFFFFFFF",
        }
    )

    v4selfclear = OrderedDict(
        {
            "DAC_CTL": "00000001",
            "SW_COARSE_CONTROL": "FFFFFFFF",
        }
    )

    v4restore = [
        ("ADC_PPER", "001E8480"),
        ("ADC_RESET", "00000000"),
    ]

    return v4regs, v4selfclear, v4restore


def potdacsetread(caObject):
    if caObject.boardname == "nova_v1":
        print_info("\n-Nova VRST check-")
        test_values = (0.0, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0)

        for a in test_values:
            caObject.setPotV("VRST", voltage=a, tune=False)
            time.sleep(0.2)
            actual = caObject.getMonV("VRST")
            delta_mv = 1000 * abs(actual - a)

            msg = "{0:.2f} : actual = {1:.5f} ; delta = {2:.2f} mV".format(
                float(a), actual, delta_mv
            )

            if abs(actual - a) > 0.05 and bool(a):
                print_warn(msg)
            else:
                print_pass(msg)

        return

    print_info("\n-VRST check-")

    for a in (0, 0.05, 0.15, 0.25, 0.5, 0.75, 1, 3, 3.5):
        caObject.setPotV("VRST", voltage=a, tune=True)
        actual = caObject.getMonV("VRST")
        delta_mv = 1000 * abs(actual - a)

        msg = "{0:.2f} : actual = {1:.5f} ; delta = {2:.2f} mV".format(
            float(a), actual, delta_mv
        )

        if abs(actual - a) > 0.05 and bool(a):
            print_warn(msg)
        else:
            print_pass(msg)


def registerRW(boardregs, boardrestore, caObject, sensorregs):
    print_info("\n\n-Verifying register read/writes-\n")

    regchecklist = OrderedDict(
        {
            "HS_TIMING_DATA_ALO": "FFFFFFFF",
            "HS_TIMING_DATA_AHI": "000000FF",
            "HS_TIMING_DATA_BLO": "FFFFFFFF",
            "HS_TIMING_DATA_BHI": "000000FF",
            "CTRL_REG": "0000000F",
            "HST_SETTINGS": "00000003",
            "TRIGGER_CTL": "00000003",
            "FPA_ROW_INITIAL": "000003FF",
            "FPA_ROW_FINAL": "000003FF",
            "FPA_FRAME_INITIAL": "00000003",
            "FPA_FRAME_FINAL": "00000003",
            "FPA_DIVCLK_EN_ADDR": "00000001",
            "FPA_OSCILLATOR_SEL_ADDR": "00000003",
            # "SUSPEND_TIME": "FFFFFFFF",
            # "DELAY_READOFF": "FFFFFFFF",
            "ADC1_CONFIG_DATA": "FFFFFFFF",
            "ADC2_CONFIG_DATA": "FFFFFFFF",
            "ADC3_CONFIG_DATA": "FFFFFFFF",
            "ADC4_CONFIG_DATA": "FFFFFFFF",
            "ADC_RESET": "0000001F",
        }
    )

    regchecklist.update(sensorregs)
    regchecklist.update(boardregs)

    checkvals = ["00000000", "FFFFFFFF"]

    for reg, mask in regchecklist.items():
        temperr = 0

        for val in checkvals:
            valmasked = "{0:0=8x}".format(int(val, 16) & int(mask, 16))

            if temperr:
                continue

            if not caObject.checkRegSet(reg, valmasked):
                temperr = 1

        if not temperr:
            print_pass("+ {: <24} - R/W OK".format(reg))
        else:
            print_fail("+ {: <24} - R/W FAIL".format(reg))

    caObject.submitMessages(boardrestore)

def test_sw_trigger_selfclear(caObject):
    """SW_TRIGGER_CONTROL bit 0 is SW_TRIG_START, which fires the trigger when
    written.  Test it separately with a generous post-write sleep so the board
    can complete the triggered acquisition before the next register access."""
    reg = "SW_TRIGGER_CONTROL"
    mask = 0x00000001  # SW_TRIG_START

    caObject.setRegister(reg, "{:08X}".format(mask))
    time.sleep(1.0)  # wait for board to finish triggered acquisition

    err, resp = caObject.getRegister(reg)
    if err or not resp:
        print_fail("+ {:<17} - self-clear READ FAIL: {}".format(reg, err))
        return

    try:
        masked = int(resp, 16) & mask
    except ValueError:
        print_fail("+ {:<17} - self-clear INVALID RESPONSE: {!r}".format(reg, resp))
        return

    if not masked:
        print_pass("+ {:<17} - self-clear OK".format(reg))
    else:
        print_fail("+ {:<17} - self-clear FAIL: 0x{:08x}".format(reg, masked))


def test_ctrl_reg_selfclear(caObject):
    reg = "CTRL_REG"
    mask = 0x00000400  # IMAGE_DATA_RETRIEVED, bit 10

    caObject.setRegister(reg, "{:08X}".format(mask))
    caObject.getRegister(reg)
    time.sleep(0.1)

    err, resp = caObject.getRegister(reg)
    if err or not resp:
        print_fail("+ CTRL_REG          - self-clear READ FAIL: {}".format(err))
        return

    masked = int(resp, 16) & mask

    if not masked:
        print_pass("+ CTRL_REG          - IMAGE_DATA_RETRIEVED self-clear OK")
    else:
        print_fail(
            "+ CTRL_REG          - IMAGE_DATA_RETRIEVED self-clear FAIL: 0x{:08x}".format(
                masked
            )
        )

def register_selfclear(boardrestore, boardselfclear, caObject):
    time.sleep(1)
    print_info("\n\n-Verifying self-clearing registers-\n")

    selfclear = OrderedDict(
        {
            "HS_TIMING_CTL": "FFFFFFFF",
            "TIMER_CTL": "FFFFFFFF",
            "ADC_CTL": "FFFFFFFF",
            "STAT_REG_SRC": "00004FFF",
            "STAT_REG2_SRC": "FFFFFFDF",
        }
    )

    selfclear.update(boardselfclear)
    test_ctrl_reg_selfclear(caObject)
    test_sw_trigger_selfclear(caObject)
    for reg, mask in selfclear.items():
        err_set, _ = caObject.setRegister(reg, "FFFFFFFF")
        if err_set:
            print_fail("+ {: <17} - self-clear SET FAIL: {}".format(reg, err_set))
            continue

        # First read may trigger/read-clear behavior.
        err_read1, _ = caObject.getRegister(reg)
        if err_read1:
            print_warn(
                "+ {: <17} - first read failed during self-clear test: {}".format(
                    reg, err_read1
                )
            )

        time.sleep(0.1)

        err_read2, resp = caObject.getRegister(reg)
        if err_read2 or not resp:
            print_fail(
                "+ {: <17} - self-clear READ FAIL: {}".format(
                    reg, err_read2 if err_read2 else "empty response"
                )
            )
            continue

        try:
            masked = int(resp, 16) & int(mask, 16)
        except ValueError:
            print_fail(
                "+ {: <17} - self-clear INVALID RESPONSE: {!r}".format(reg, resp)
            )
            continue

        if not masked:
            print_pass("+ {: <17} - self-clear OK".format(reg))
        else:
            print_fail(
                "+ {: <17} - self-clear FAIL: ".format(reg)
                + "0x"
                + "{0:0=8x}".format(masked)
            )

    caObject.submitMessages(boardrestore)


def statusDump(caObject):
    time.sleep(1)
    print_info("\n\n-Status dump-\n")
    print(json.dumps(caObject.dumpStatus(), indent=4))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "-b",
        action="store",
        dest="board",
        default="llnl_v4",
        help="name of board: 'llnl_v1', 'llnl_v4', or 'nova_v1'",
    )
    parser.add_argument(
        "-c",
        action="store",
        dest="comm",
        default="GigE",
        help="name of comm: 'GigE' or 'RS422'",
    )
    parser.add_argument(
        "-s",
        action="store",
        dest="sensor",
        default="icarus2",
        help="name of sensor: 'icarus', 'icarus2', 'hyperion', or 'daedalus'",
    )
    parser.add_argument(
        "-p",
        action="store",
        dest="portNum",
        default=None,
        help="specified port number",
    )
    parser.add_argument(
        "-i",
        action="store",
        dest="ipAdd",
        default=None,
        help="specified ip address",
    )
    parser.add_argument(
        "-v",
        action="store",
        dest="verbose",
        default=4,
        type=int,
        help="verbosity level",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="run automatically without interaction",
    )
    parser.add_argument(
        "--hw",
        action="store_true",
        help="wait for hardware triggering",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="disable ANSI colorized terminal output",
    )

    args = parser.parse_args()

    if args.no_color:
        USE_COLOR = False

    testSuite(
        interactive=not args.batch,
        swtrigger=not args.hw,
        board=args.board,
        comm=args.comm,
        sensor=args.sensor,
        portNum=args.portNum,
        ipAdd=args.ipAdd,
        verbose=args.verbose,
    )

"""
Copyright (c) 2025, Lawrence Livermore National Security, LLC.  All rights reserved.

This work was produced at the Lawrence Livermore National Laboratory (LLNL) under
contract no. DE-AC52-07NA27344 (Contract 44) between the U.S. Department of Energy (DOE)
and Lawrence Livermore National Security, LLC (LLNS) for the operation of LLNL.
'nsCamera' is distributed under the terms of the MIT license. All new contributions must
be made under this license.
"""