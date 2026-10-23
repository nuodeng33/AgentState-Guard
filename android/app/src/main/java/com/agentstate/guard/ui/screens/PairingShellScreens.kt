package com.agentstate.guard.ui.screens

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.core.content.ContextCompat
import com.agentstate.guard.R
import com.agentstate.guard.ui.theme.Spacing
import com.agentstate.guard.ui.link.decodeInvitationImage
import com.agentstate.guard.ui.link.QrImageReadResult
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import com.google.zxing.client.android.Intents
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions

/**
 * Bundled QR scanner, independent of Google Play services and module downloads.
 * CAMERA is requested only by the explicit Scan action. No image or payload is saved/logged.
 */
@Composable
fun ScanConnectScreen(
    onBack: () -> Unit,
    onPayload: (String) -> Unit,
    busy: Boolean = false,
    failureCode: String? = null,
) {
    val context = LocalContext.current
    val payloadReceiver by rememberUpdatedState(onPayload)
    var scanning by remember { mutableStateOf(false) }
    var importing by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    var scanFailure by remember { mutableStateOf<String?>(null) }
    val prompt = stringResource(R.string.pairing_scan_title)
    val scanner = rememberLauncherForActivityResult(ScanContract()) { result ->
        scanning = false
        if (result.originalIntent?.getBooleanExtra(Intents.Scan.MISSING_CAMERA_PERMISSION, false) == true) {
            scanFailure = "CAMERA_PERMISSION_DENIED"
        } else {
            result.contents?.let { payload ->
                if (payload.isBlank()) scanFailure = "PAIR_QR_INVALID" else payloadReceiver(payload)
            }
        }
    }
    val launchCapture = {
        try {
            scanner.launch(ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                .setPrompt(prompt).setBeepEnabled(false).setBarcodeImageEnabled(false)
                .setOrientationLocked(false)
                .addExtra(Intents.Scan.SHOW_MISSING_CAMERA_PERMISSION_DIALOG, false))
        } catch (_: Exception) {
            scanning = false
            scanFailure = "CAMERA_UNAVAILABLE"
        }
    }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) launchCapture() else {
            scanning = false
            scanFailure = "CAMERA_PERMISSION_DENIED"
        }
    }
    val imagePicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri == null) importing = false else scope.launch {
            try {
                val result = withContext(Dispatchers.IO) {
                    try {
                        context.contentResolver.openInputStream(uri)?.use { decodeInvitationImage(it) }
                            ?: QrImageReadResult(reasonCode = "QR_IMAGE_UNREADABLE")
                    } catch (_: Exception) {
                        QrImageReadResult(reasonCode = "QR_IMAGE_UNREADABLE")
                    }
                }
                if (result.payload != null) payloadReceiver(result.payload) else scanFailure = result.reasonCode
            } catch (cancelled: CancellationException) {
                throw cancelled
            } finally {
                importing = false
            }
        }
    }
    Column(
        modifier = Modifier.fillMaxSize().padding(Spacing.l),
        verticalArrangement = Arrangement.spacedBy(Spacing.m),
    ) {
        Text(stringResource(R.string.pairing_scan_title), style = MaterialTheme.typography.headlineSmall)
        Text(stringResource(R.string.pairing_scan_note))
        Button(
            enabled = !busy && !scanning && !importing,
            modifier = Modifier.fillMaxWidth(),
            onClick = {
                if (!busy && !scanning && !importing) {
                    scanFailure = null
                    if (!context.packageManager.hasSystemFeature(PackageManager.FEATURE_CAMERA_ANY)) {
                        scanFailure = "CAMERA_UNAVAILABLE"
                    } else {
                        scanning = true
                        if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
                            launchCapture()
                        } else permission.launch(Manifest.permission.CAMERA)
                    }
                }
            },
        ) { Text(stringResource(if (busy || scanning) R.string.state_loading else R.string.action_scan_qr)) }
        Button(
            enabled = !busy && !scanning && !importing,
            modifier = Modifier.fillMaxWidth(),
            onClick = {
                if (!busy && !scanning && !importing) {
                    importing = true
                    scanFailure = null
                    try { imagePicker.launch(arrayOf("image/*")) }
                    catch (_: Exception) { importing = false; scanFailure = "QR_IMAGE_UNREADABLE" }
                }
            },
        ) { Text(stringResource(if (importing) R.string.pairing_image_loading else R.string.pairing_image_action)) }
        val failure = scanFailure ?: failureCode
        failure?.let {
            Text(it, color = MaterialTheme.colorScheme.error)
            if (it.startsWith("QR_IMAGE_")) Text(stringResource(if (it == "QR_IMAGE_TOO_LARGE") R.string.pairing_image_too_large else R.string.pairing_image_failed))
            if (it == "CAMERA_PERMISSION_DENIED") Text(stringResource(R.string.pairing_camera_denied))
            if (it == "CAMERA_UNAVAILABLE") Text(stringResource(R.string.pairing_camera_unavailable))
        }
        OutlinedButton(onClick = onBack, enabled = !busy && !scanning && !importing, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.action_back))
        }
    }
}
