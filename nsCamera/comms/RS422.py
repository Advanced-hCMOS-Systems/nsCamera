# -*- coding: utf-8 -*-
"""
RS422 driver for nsCamera

Author: Brad Funsten (funsten1@llnl.gov)
Author: Jeremy Martin Hill (jerhill@llnl.gov)

Copyright (c) 2025, Lawrence Livermore National Security, LLC.  All rights reserved.
LLNL-CODE-838080

This work was produced at the Lawrence Livermore National Laboratory (LLNL) under 
contract no. DE-AC52-07NA27344 (Contract 44) between the U.S. Department of Energy (DOE)
and Lawrence Livermore National Security, LLC (LLNS) for the operation of LLNL.
'nsCamera' is distributed under the terms of the MIT license. All new contributions must
be made under this license.

Version: 2.1.4 (May 2026) - AHS RS422 fix for stable comms
"""

import logging
import sys
import time  # to time the script

import serial
import serial.tools.list_ports  # for RS422 serial link setup

from nsCamera.utils.misc import generateFrames, str2bytes, bytes2str, checkCRC


class RS422:
    """
    Code to manage RS422 connection. Will automatically query available COM interfaces
      until a board is found. Use the 'port=x' parameter in cameraAssembler call to
      specify a particular COM interface.

    Exposed methods:
        arm() - Puts camera into wait state for external trigger
        readFrames() - waits for data ready register flag, then copies camera image data
          into numpy arrays
        readoff() - waits for data ready register flag, then copies camera image data
          into numpy arrays; returns payload, payload size, and error message
        sendCMD(pkt) - sends packet object via serial port
        readSerial(size, timeout) - read 'size' bytes from serial port
        writeSerial(cmd) - submits string 'cmd' (assumes string is preformed packet)
        closeDevice() - close serial connections
    """

    def __init__(self, camassem, baud=None, par="N", stop=1):
        """
        Args:
            camassem: parent cameraAssembler object
            baud: bits per second; defaults to 4000000 for nova_v1 (tested with
                FTDI USB-COM422-PLUS2), 921600 for all other boards
            par: parity type. Default changed to "N" for 921600 8N1.
            stop: number of stop bits
        """
        self.ca = camassem
        if baud is None:
            baud = 4000000 if getattr(camassem, "boardname", "").lower() == "nova_v1" else 921600
        self.logcrit = self.ca.logcritbase + "[RS422] "
        self.logerr = self.ca.logerrbase + "[RS422] "
        self.logwarn = self.ca.logwarnbase + "[RS422] "
        self.loginfo = self.ca.loginfobase + "[RS422] "
        self.logdebug = self.ca.logdebugbase + "[RS422] "
        logging.info(self.loginfo + "initializing RS422 comms object")
        logging.debug(
            self.logdebug
            + "Init: baud = "
            + str(baud)
            + "; par = "
            + str(par)
            + "; stop = "
            + str(stop)
        )
        self.mode = 0
        self.baud = baud
        self.par = par
        self.stop = stop
        self.read_timeout = 3
        self.write_timeout = 1
        self.serial_chunk_timeout = 0.01
        self.tx_settle = 0.01
        self.rx_quiet_s = 0.05
        self.rx_drain_max_s = 0.5
        self.regular_settle_s = 0.01
        self.regular_timeout_s = 1.0
        self.regular_tries = 8
        self.rs422_stats = {
            "regular_tx_count": 0,
            "regular_attempt_count": 0,
            "regular_retry_count": 0,
            "regular_short_read_count": 0,
            "regular_wrong_length_count": 0,
            "regular_crc_fail_count": 0,
            "regular_bad_frame_count": 0,
            "regular_success_count": 0,
            "regular_fail_count": 0,
            "regular_max_attempts_used": 0,
            "payload_tx_count": 0,
            "payload_attempt_count": 0,
            "payload_retry_count": 0,
            "payload_short_read_count": 0,
            "payload_preface_crc_fail_count": 0,
            "payload_crc_fail_count": 0,
            "payload_success_count": 0,
            "payload_fail_count": 0,
            "payload_max_attempts_used": 0,
        }
        self.datatimeout = 90
        logging.debug(
            self.logdebug + "Data timeout = " + str(self.datatimeout) + " seconds"
        )
        self.PY3 = sys.version_info > (3,)
        self.skipError = False
        self._ser = None

        port = ""
        ports = list(serial.tools.list_ports.comports())
        logging.debug(self.logdebug + "Comports: " + str(ports))
        requested = str(self.ca.port) if self.ca.port is not None else None

        for p, desc, add in ports:
            if not self._port_matches_request(p, requested):
                continue
            logging.info(self.loginfo + "found comm port " + p)
            try:
                ser = self._open_serial(p, timeout=self.serial_chunk_timeout)

                # Board-identification probe. Single attempt.
                # Expected response to read command 0x1 is command 0x9.
                resp = ""
                probe_ok = False
                for probe_attempt in range(1):
                    err, resp = self._regular_transaction_raw(
                        ser,
                        "aaaa1000000000001a84",
                        timeout_s=self.read_timeout,
                        settle_s=self.tx_settle,
                    )
                    logging.debug(self.logdebug + "Init response: " + str(resp))

                    if err:
                        continue
                    if len(resp) != 20:
                        continue
                    if resp[0:4].lower() != "aaaa" or resp[4].lower() != "9":
                        continue
                    if not checkCRC(resp[4:20]):
                        logging.error(self.logerr + "Init probe CRC fail: " + resp)
                        continue
                    probe_ok = True
                    break

                if probe_ok:
                    boardid = resp[8:10]
                    if boardid == "00":
                        logging.critical(
                            self.logcrit + "SNLrevC board detected - not compatible with nsCamera >= 2.0"
                        )
                        ser.close()
                        sys.exit(1)
                    elif boardid == "81":
                        logging.info(self.loginfo + "LLNLv1 board detected")
                    elif boardid == "84":
                        logging.info(self.loginfo + "LLNLv4 board detected")
                    elif boardid == "05":
                        logging.info(self.loginfo + "AHS Nova_v1 board detected")
                    else:
                        logging.info(self.loginfo + "unidentified board detected")
                    logging.info(self.loginfo + "connected to " + p)
                    port = p
                    self._ser = ser
                    break
                ser.close()
            except Exception as e:
                try:
                    ser.close()
                except Exception:
                    pass
                logging.error(self.logerr + "port identification: " + str(e))

        if port == "":
            if self.ca.port:
                logging.critical(
                    self.logcrit + "No usable board found at port " + str(self.ca.port)
                )
                sys.exit(1)
            else:
                logging.critical(self.logcrit + "No usable board found")
                sys.exit(1)

        self.port = port
        self.ca.port = port
        self.payloadsize = (
            self.ca.sensor.width
            * self.ca.sensor.height
            * self.ca.sensor.nframes
            * self.ca.sensor.bytesperpixel
        )
        logging.debug(
            self.logdebug + "Payload size: " + str(self.payloadsize) + " bytes"
        )
        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()
        if not self._ser.is_open:
            logging.critical(self.logcrit + "Unable to open serial connection")
            sys.exit(1)

    def resetRS422Stats(self):
        """Reset silent RS422 regular-command quality counters."""
        for key in self.rs422_stats:
            self.rs422_stats[key] = 0

    def _port_matches_request(self, port_name, requested):
        if requested is None:
            return True
        requested = str(requested)
        return (
            port_name == requested
            or port_name.endswith(requested)
            or port_name == "COM" + requested
            or port_name.endswith("." + requested)
            or port_name.endswith("-" + requested)
        )

    def _open_serial(self, port_name, timeout=None):
        if timeout is None:
            timeout = self.serial_chunk_timeout
        return serial.Serial(
            port=port_name,
            baudrate=self.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE if str(self.par).upper() == "N" else self.par,
            stopbits=serial.STOPBITS_ONE if int(self.stop) == 1 else self.stop,
            timeout=timeout,
            write_timeout=self.write_timeout,
            inter_byte_timeout=None,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
            exclusive=True,
        )

    def _read_exact_raw(self, ser, nbytes, timeout_s):
        """Read exactly nbytes using the same behavior as the proven standalone util.py.

        Important: do not temporarily change/restore ser.timeout here. The standalone
        path leaves the serial object configured with timeout=0.05 and simply loops
        until the absolute deadline. Earlier nsCamera patches restored timeouts around
        reads; the standalone-on-nsCamera-serial test proved the plain util behavior is
        clean on this same live serial object.
        """
        if ser.timeout != self.serial_chunk_timeout:
            ser.timeout = self.serial_chunk_timeout

        deadline = time.time() + timeout_s
        rx = b""
        while len(rx) < nbytes and time.time() < deadline:
            chunk = ser.read(nbytes - len(rx))
            if chunk:
                rx += chunk
        return rx

    def _drain_quiet(self, ser, quiet_s=None, max_s=None, log=True):
        """Drain stale/late RX bytes until the serial input has been quiet."""
        if quiet_s is None:
            quiet_s = self.rx_quiet_s
        if max_s is None:
            max_s = self.rx_drain_max_s
        old_timeout = ser.timeout
        ser.timeout = self.serial_chunk_timeout
        deadline = time.time() + max_s
        quiet_deadline = time.time() + quiet_s
        drained = b""
        try:
            while time.time() < deadline:
                try:
                    waiting = ser.in_waiting
                except Exception:
                    waiting = 0
                if waiting:
                    chunk = ser.read(waiting)
                    if chunk:
                        drained += chunk
                        quiet_deadline = time.time() + quiet_s
                elif time.time() >= quiet_deadline:
                    break
                else:
                    time.sleep(0.005)
        finally:
            ser.timeout = old_timeout
        if drained and log:
            logging.debug(self.logdebug + "drainQuiet: drained " + str(len(drained)) + " bytes: " + bytes2str(drained))
        return drained

    def _regular_transaction_raw(self, ser, pktStr, timeout_s=None, settle_s=None):
        """Exact util.py-style regular transaction on an already-open serial port.

        This is deliberately boring and mirrors the proven standalone path:
            reset_input_buffer(); sleep(settle); reset_output_buffer(); write();
            flush(); read_exact(10).

        No nsCamera writeSerial/readSerial wrappers. No quiet drain. No per-attempt
        logging. No timeout restore. The user proved util.send_regular_packet() is
        clean even when using this same nsCamera-owned serial object, so keep this path
        as close to util.py as possible.
        """
        if timeout_s is None:
            timeout_s = self.regular_timeout_s
        if settle_s is None:
            settle_s = self.regular_settle_s

        self.rs422_stats["regular_attempt_count"] += 1

        ser.reset_input_buffer()
        time.sleep(settle_s)
        ser.reset_output_buffer()
        ser.write(bytes.fromhex(pktStr))
        ser.flush()

        rx = self._read_exact_raw(ser, 10, timeout_s)
        resp = rx.hex()
        if len(rx) != 10:
            self.rs422_stats["regular_short_read_count"] += 1
            return (
                self.logerr
                + "readSerial: short read; expected 10 bytes, got "
                + str(len(rx))
                + " bytes: '"
                + resp
                + "'"
            ), resp
        return "", resp


    def _payload_transaction_raw(self, ser, pktStr, nbytes, timeout_s=None, settle_s=None):
        """Exact util.py-style burst/payload transaction on an already-open serial port.

        Keep ser.timeout at the normal short chunk timeout and loop until an
        absolute deadline. Do not temporarily change/restore ser.timeout.
        """
        if timeout_s is None:
            timeout_s = self._payload_timeout(nbytes)
        if settle_s is None:
            settle_s = self.regular_settle_s

        self.rs422_stats["payload_attempt_count"] += 1

        ser.reset_input_buffer()
        time.sleep(settle_s)
        ser.reset_output_buffer()
        ser.write(bytes.fromhex(pktStr))
        ser.flush()

        rx = self._read_exact_raw(ser, nbytes, timeout_s)
        resp = rx.hex()
        if len(rx) != nbytes:
            self.rs422_stats["payload_short_read_count"] += 1
            return (
                self.logerr
                + "readSerial(payload): short read; expected "
                + str(nbytes)
                + " bytes, got "
                + str(len(rx))
                + " bytes"
            ), resp
        return "", resp

    def _payload_timeout(self, nbytes, margin=4.0, overhead=5.0, minimum=5.0):
        """Timeout scaled to expected transfer size and baud rate.

        Replaces the fixed datatimeout for burst reads so a truncated transfer
        triggers retry in O(transfer_time) rather than a fixed 90 s.
        """
        transfer_s = nbytes / (self.baud / 10.0)  # 8N1: 10 bits per byte
        return max(transfer_s * margin + overhead, minimum)

    def serialClose(self):
        """
        Close serial interface
        """
        logging.debug(self.logdebug + "serialclose")
        self._ser.close()  # close serial interface COM port

    def sendCMD(self, pkt):
        """
        Submit packet and verify response packet. Recognizes readoff packet and adjusts
        read size and timeout appropriately.
        """
        pktStr = pkt.pktStr()
        logging.debug(self.logdebug + "sendCMD packet: " + str(pktStr))
        err0 = ""
        err = ""
        resp = ""
        tries = self.regular_tries

        if (
            hasattr(self.ca, "board")
            and pktStr[4] == "0"
            and pktStr[5:8] == self.ca.board.registers["SRAM_CTL"]
        ):
            logging.info(
                self.loginfo + "Payload size (bytes) = " + str(self.payloadsize)
            )
            crcresp0 = ""
            crcresp1 = ""
            smallresp = ""
            emptyResponse = False
            wrongSize = False
            payload_tries = 3
            self.ca.payloaderror = False
            self.rs422_stats["payload_tx_count"] += 1
            for i in range(payload_tries):
                if i:
                    self.rs422_stats["payload_retry_count"] += 1
                err, resp = self._payload_transaction_raw(
                    self._ser,
                    pktStr,
                    self.payloadsize + 20,
                    timeout_s=self.datatimeout,
                    settle_s=self.regular_settle_s,
                )
                if err:
                    logging.error(
                        self.logerr + "sendCMD: read payload failed " + pktStr + err
                    )
                    self.ca.payloaderror = True
                else:
                    if not len(resp):
                        err0 = self.logerr + "sendCMD: empty response from board"
                        logging.error(err0)
                        emptyResponse = True
                        self.ca.payloaderror = True
                    elif len(resp) != 2 * (self.payloadsize + 20):
                        err0 = (
                            self.logerr
                            + "sendCMD: incorrect response; expected "
                            + str(self.payloadsize + 20)
                            + " bytes, received "
                            + str(len(resp) // 2)
                        )
                        logging.error(err0)
                        wrongSize = True
                        smallresp = resp
                        self.ca.payloaderror = True
                    elif not checkCRC(resp[4:20]):
                        self.rs422_stats["payload_preface_crc_fail_count"] += 1
                        err0 = (
                            self.logerr
                            + "sendCMD: "
                            + pktStr
                            + " - payload preface CRC fail"
                        )
                        logging.error(err0)
                        self.ca.payloaderror = True
                        crcresp1 = resp
                    elif not checkCRC(resp[24:]):
                        self.rs422_stats["payload_crc_fail_count"] += 1
                        err0 = self.logerr + "sendCMD: " + pktStr + " - payload CRC fail"
                        logging.error(err0)
                        self.ca.payloaderror = True
                        crcresp0 = resp
                    err += err0
                if self.ca.payloaderror:
                    if i == payload_tries - 1:
                        self.rs422_stats["payload_fail_count"] += 1
                        if (i + 1) > self.rs422_stats["payload_max_attempts_used"]:
                            self.rs422_stats["payload_max_attempts_used"] = i + 1
                        if crcresp0:
                            logging.error(
                                self.logerr + "sendCMD: Unable to acquire CRC-confirmed payload after "
                                + str(payload_tries)
                                + " attempts. Continuing with unconfirmed payload"
                            )
                            resp = crcresp0
                        elif crcresp1:
                            logging.error(
                                self.logerr + "sendCMD: Unable to acquire CRC-confirmed readoff after "
                                + str(payload_tries)
                                + " attempts. Continuing with unconfirmed payload"
                            )
                            resp = crcresp1
                        elif wrongSize:
                            logging.error(
                                self.logerr + "sendCMD: Unable to acquire complete payload after "
                                + str(payload_tries)
                                + " attempts. Dumping datastream to file."
                            )
                            resp = smallresp
                            self.ca.dumpNumpy(resp)
                        elif emptyResponse:
                            logging.error(
                                self.logerr + "sendCMD: Unable to acquire any payload after "
                                + str(payload_tries)
                                + " attempts."
                            )
                    else:
                        logging.info(self.loginfo + "Retrying download, attempt #" + str(i + 2))
                        err = ""
                        err0 = ""
                        self.ca.payloaderror = False
                        time.sleep(0.02)
                else:
                    self.rs422_stats["payload_success_count"] += 1
                    if (i + 1) > self.rs422_stats["payload_max_attempts_used"]:
                        self.rs422_stats["payload_max_attempts_used"] = i + 1
                    logging.info(self.loginfo + "Download successful")
                    break
        else:
            self.rs422_stats["regular_tx_count"] += 1
            last_err = ""
            last_resp = ""
            attempts_used = 0
            for attempt in range(tries):
                attempts_used = attempt + 1
                if attempt:
                    self.rs422_stats["regular_retry_count"] += 1
                    logging.debug(
                        self.logdebug
                        + "sendCMD: retrying regular packet, attempt "
                        + str(attempt + 1)
                        + "/"
                        + str(tries)
                    )
                    time.sleep(0.05)
                err, resp = self._regular_transaction_raw(
                    self._ser,
                    pktStr,
                    timeout_s=self.regular_timeout_s,
                    settle_s=self.regular_settle_s,
                )
                logging.debug(self.logdebug + "sendCMD response: " + str(resp))
                last_err = err
                last_resp = resp
                if err:
                    logging.debug(self.logdebug + "sendCMD: readSerial failed (regular packet) " + err)
                    continue
                if len(resp) != 20:
                    self.rs422_stats["regular_wrong_length_count"] += 1
                    err = self.logerr + "sendCMD- regular packet wrong length: " + resp
                    logging.debug(err)
                    last_err = err
                    continue
                # Expected response command is request command ORed with 0x8:
                #   read  command 0x1 -> response 0x9
                #   write command 0x0 -> response 0x8
                try:
                    expected_resp_cmd = format(int(pktStr[4], 16) | 0x8, "x")
                except Exception:
                    expected_resp_cmd = ""
                if resp[0:4].lower() != "aaaa" or resp[4].lower() != expected_resp_cmd:
                    err = (
                        self.logerr
                        + "sendCMD- regular packet bad response frame: "
                        + resp
                        + "; expected response cmd "
                        + expected_resp_cmd
                    )
                    self.rs422_stats["regular_bad_frame_count"] += 1
                    logging.debug(err)
                    last_err = err
                    continue
                if not checkCRC(resp[4:20]):
                    self.rs422_stats["regular_crc_fail_count"] += 1
                    err = self.logerr + "sendCMD- regular packet CRC fail: " + resp
                    logging.debug(err)
                    last_err = err
                    continue
                self.rs422_stats["regular_success_count"] += 1
                if attempts_used > self.rs422_stats["regular_max_attempts_used"]:
                    self.rs422_stats["regular_max_attempts_used"] = attempts_used
                err = ""
                break
            else:
                self.rs422_stats["regular_fail_count"] += 1
                if attempts_used > self.rs422_stats["regular_max_attempts_used"]:
                    self.rs422_stats["regular_max_attempts_used"] = attempts_used
                err = last_err
                resp = last_resp
                if err:
                    logging.error(err)
        return err, resp

    def arm(self, mode):
        """
        Puts camera into wait state for trigger. Mode determines source; defaults to
          'Hardware'

        Args:
            mode:   'Software'|'S' activates software, disables hardware triggering
                    'Hardware'|'H' activates hardware, disables software triggering
                      Hardware is the default

        Returns:
            tuple (error, response string)
        """
        if not mode:
            mode = "Hardware"
        logging.info(self.loginfo + "arm")
        logging.debug(self.logdebug + "arming mode: " + str(mode))
        self.ca.clearStatus()
        self.ca.latchPots()
        err, resp = self.ca.startCapture(mode)
        if err:
            logging.error(self.logerr + "unable to arm camera")
        else:
            self.ca.armed = True
            self.skipError = True
        return err, resp

    def readFrames(self, waitOnSRAM, timeout=0, fast=False, columns=1):
        """
        Copies image data from board into numpy arrays.

        Args:
            waitOnSRAM: if True, wait until SRAM_READY flag is asserted to begin copying
              data
            timeout: passed to waitForSRAM; after this many seconds begin copying data
              irrespective of SRAM_READY status; 'zero' means wait indefinitely
              WARNING: If acquisition fails, the SRAM will not contain a current image,
                but the code will copy the data anyway
            fast: if False, parse and convert frames to numpy arrays; if True, return
              unprocessed text stream
            columns: 1 for single image per frame, 2 for separate hemisphere images

        Returns:
            list of numpy arrays OR raw text stream

        """
        frames, _, _ = self.readoff(waitOnSRAM, timeout, fast, columns)
        return frames

    def readoff(self, waitOnSRAM, timeout, fast, columns=1):
        """
        Copies image data from board into numpy arrays; returns data, length of data,
        and error messages. Use 'readFrames()' unless you require this additional
        information

        Args:
            waitOnSRAM: if True, wait until SRAM_READY flag is asserted to begin copying
              data
            timeout: passed to waitForSRAM; after this many seconds begin copying data
              irrespective of SRAM_READY status; 'zero' means wait indefinitely
              WARNING: If acquisition fails, the SRAM will not contain a current image,
                but the code will copy the data anyway
            fast: if False, parse and convert frames to numpy arrays; if True, return
              unprocessed text stream
            columns: 1 for single image per frame, 2 for separate hemisphere images

        Returns:
            tuple (list of numpy arrays OR raw text stream, length of downloaded payload
              in bytes, payload error flag)
            NOTE: This reduces readoff by <1 second, so will have no noticeable impact
              when using RS422
        """
        logging.info(self.loginfo + "readoff")
        logging.debug(
            self.logdebug
            + "readoff: waitonSRAM = "
            + str(waitOnSRAM)
            + "; timeout = "
            + str(timeout)
            + "; fast = "
            + str(fast)
        )
        errortemp = False

        # Wait for data to be ready on board, turns off error messaging
        # Skip wait only if explicitly tagged 'False' ('None' defaults to True)
        if waitOnSRAM is not False:
            logging.getLogger().setLevel(logging.CRITICAL)
            self.ca.waitForSRAM(timeout)
            logging.getLogger().setLevel(self.ca.verblevel)

        # Retrieve data
        err, rval = self.ca.readSRAM()
        if err:
            logging.error(self.logerr + "Error detected in readSRAM")
        time.sleep(0.3)
        logging.debug(self.logdebug + "readoff: first 64 chars: " + str(rval[0:64]))
        # extract only the read burst data. Remove header & CRC footer
        read_burst_data = rval[36:-4]

        # Payload size as string implied by provided parameters
        expectedlength = (
            4
            * (self.ca.sensor.lastframe - self.ca.sensor.firstframe + 1)
            * (self.ca.sensor.lastrow - self.ca.sensor.firstrow + 1)
            * self.ca.sensor.width
        )
        padding = expectedlength - len(read_burst_data)
        if padding:
            logging.warning(
                "{logwarn}readoff: Payload is shorter than expected."
                " Padding with '0's".format(logwarn=self.logwarn)
            )
            read_burst_data = read_burst_data.ljust(expectedlength, "0")

        if fast:
            return read_burst_data, len(read_burst_data) // 2, errortemp
        else:
            parsed = generateFrames(self.ca, read_burst_data, columns)
            return parsed, len(read_burst_data) // 2, errortemp

    def writeSerial(self, outstring, timeout=None):
        """
        Transmit string to board.
        """
        logging.debug(
            self.logdebug
            + "writeSerial: outstring = "
            + str(outstring)
            + "; timeout = "
            + str(timeout)
        )
        old_write_timeout = self._ser.write_timeout
        if timeout is not None:
            self._ser.write_timeout = timeout
        else:
            self._ser.write_timeout = self.write_timeout
        try:
            lengthwritten = self._ser.write(str2bytes(outstring))
            self._ser.flush()
        finally:
            self._ser.write_timeout = old_write_timeout
        return lengthwritten

    def readSerial(self, size, timeout=None):
        """
        Read exactly 'size' bytes from the serial port using a deadline loop.
        """
        logging.debug(
            self.logdebug
            + "readSerial: size = "
            + str(size)
            + "; timeout = "
            + str(timeout)
        )
        if timeout is None:
            timeout = self.read_timeout
        resp_bytes = self._read_exact_raw(self._ser, size, timeout)
        resp = bytes2str(resp_bytes)
        err = ""
        if len(resp_bytes) != size:
            err = (
                self.logerr
                + "readSerial: short read; expected "
                + str(size)
                + " bytes, got "
                + str(len(resp_bytes))
                + " bytes: '"
                + resp
                + "'"
            )
            logging.error(err)
        return err, resp

    def closeDevice(self):
        """
        Close primary serial interface
        """
        logging.debug(self.logdebug + "Closing RS422 connection")
        self._ser.close()


"""
Copyright (c) 2025, Lawrence Livermore National Security, LLC.  All rights reserved.  
LLNL-CODE-838080

This work was produced at the Lawrence Livermore National Laboratory (LLNL) under 
contract no. DE-AC52-07NA27344 (Contract 44) between the U.S. Department of Energy (DOE)
and Lawrence Livermore National Security, LLC (LLNS) for the operation of LLNL.
'nsCamera' is distributed under the terms of the MIT license. All new contributions must
be made under this license.
"""
