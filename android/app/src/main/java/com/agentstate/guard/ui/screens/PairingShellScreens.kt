package com.agentstate.guard.ui.screens

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
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import com.agentstate.guard.R
import com.agentstate.guard.ui.theme.Spacing
import com.google.android.gms.common.api.CommonStatusCodes
import com.google.android.gms.common.api.ApiException
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.barcode.common.Barcode

/**
 * Delegated Google code scanner: QR only, explicit launch, no app CAMERA permission.
 * The opaque invitation goes to the existing adapter; images and QR contents are never logged.
 */
@Composable
fun ScanConnectScreen(
    onBack: () -> Unit,
    onPayload: (String) -> Unit,
    busy: Boolean = false,
    failureCode: String? = null,
) {
    val context = LocalContext.current
    var scanning by remember { mutableStateOf(false) }
    var scanFailure by remember { mutableStateOf<String?>(null) }
    val active = remember { booleanArrayOf(true) }
    DisposableEffect(Unit) { active[0] = true; onDispose { active[0] = false } }
    val scanner = remember(context) {
        GmsBarcodeScanning.getClient(context, GmsBarcodeScannerOptions.Builder()
            .setBarcodeFormats(Barcode.FORMAT_QR_CODE).enableAutoZoom().build())
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
                scanning = true; scanFailure = null
                scanner.startScan()
                    .addOnSuccessListener { result ->
                        if (active[0]) {
                            scanning = false
                            val payload = result.rawValue
                            if (payload.isNullOrBlank()) scanFailure = "PAIR_QR_INVALID" else onPayload(payload)
                        }
                    }
                    .addOnCanceledListener { if (active[0]) scanning = false }
                    .addOnFailureListener { error ->
                        if (active[0]) {
                            scanning = false
                            if ((error as? ApiException)?.statusCode != CommonStatusCodes.CANCELED) {
                                scanFailure = "SCANNER_UNAVAILABLE"
                            }
                        }
                    }
            },
        ) { Text(stringResource(if (busy || scanning) R.string.state_loading else R.string.action_scan_qr)) }
        (failureCode ?: scanFailure)?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        OutlinedButton(onClick = onBack, enabled = !busy && !scanning, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.action_back))
        }
    }
}
