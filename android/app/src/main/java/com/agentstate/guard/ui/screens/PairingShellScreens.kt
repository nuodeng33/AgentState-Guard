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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.core.content.ContextCompat
import com.agentstate.guard.R
import com.agentstate.guard.ui.theme.Spacing
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
    Column(
        modifier = Modifier.fillMaxSize().padding(Spacing.l),
        verticalArrangement = Arrangement.spacedBy(Spacing.m),
    ) {
        Text(stringResource(R.string.pairing_scan_title), style = MaterialTheme.typography.headlineSmall)
        Text(stringResource(R.string.pairing_scan_note))
        Button(
            enabled = !busy && !scanning,
            modifier = Modifier.fillMaxWidth(),
            onClick = {
                if (!busy && !scanning) {
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
        val failure = failureCode ?: scanFailure
        failure?.let {
            Text(it, color = MaterialTheme.colorScheme.error)
            if (it == "CAMERA_PERMISSION_DENIED") Text(stringResource(R.string.pairing_camera_denied))
            if (it == "CAMERA_UNAVAILABLE") Text(stringResource(R.string.pairing_camera_unavailable))
        }
        OutlinedButton(onClick = onBack, enabled = !busy && !scanning, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.action_back))
        }
    }
}
