package com.agentstate.guard.ui.screens

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.core.content.ContextCompat
import com.agentstate.guard.R
import com.agentstate.guard.qr.QrScanView
import com.agentstate.guard.ui.theme.Spacing
import com.agentstate.guard.ui.theme.TextSecondary

/**
 * Scan-to-connect. Under CAMERA permission: the live CameraX viewfinder
 * feeds the ZXing scanner; whatever decodes is reported verbatim — only
 * App.kt turns it into pairing actions. Without permission: the normal
 * Android runtime UX (rationale -> system dialog -> "open settings" after
 * permanent denial). Nothing in this screen fabricates a payload or a
 * manual-IP/manual-entry escape hatch — scanning is the only way in.
 */
@Composable
fun ScanConnectScreen(
    onBack: () -> Unit,
    onPayloadDetected: (String) -> Unit,
) {
    val context = LocalContext.current
    var hasCameraPermission by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
                PackageManager.PERMISSION_GRANTED,
        )
    }
    var permanentlyDenied by remember { mutableStateOf(false) }

    val launcher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        hasCameraPermission = granted
        permanentlyDenied = !granted &&
            (context as? android.app.Activity)?.shouldShowRequestPermissionRationale(
                Manifest.permission.CAMERA,
            ) == false
    }

    Column(modifier = Modifier.fillMaxSize()) {
        when {
            hasCameraPermission -> {
                Box(modifier = Modifier.weight(1f)) {
                    QrScanView(onPayloadText = onPayloadDetected, onInvalid = { })
                }
                OutlinedButton(
                    onClick = onBack,
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(Spacing.l),
                ) {
                    Text(stringResource(R.string.action_back))
                }
            }
            else -> {
                Column(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(Spacing.l),
                    verticalArrangement = Arrangement.spacedBy(Spacing.m),
                ) {
                    Text(
                        stringResource(R.string.pairing_scan_title),
                        style = MaterialTheme.typography.headlineSmall,
                    )
                    Text(
                        stringResource(
                            if (permanentlyDenied) R.string.camera_permission_permanent_body
                            else R.string.camera_permission_rationale_body,
                        ),
                        style = MaterialTheme.typography.bodyMedium,
                        color = TextSecondary,
                    )
                    if (permanentlyDenied) {
                        Button(
                            onClick = {
                                val intent = Intent(
                                    Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                                    Uri.fromParts("package", context.packageName, null),
                                )
                                context.startActivity(intent)
                            },
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Text(stringResource(R.string.camera_permission_open_settings))
                        }
                    } else {
                        Button(
                            onClick = { launcher.launch(Manifest.permission.CAMERA) },
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Text(stringResource(R.string.camera_permission_grant))
                        }
                    }
                    OutlinedButton(onClick = onBack, modifier = Modifier.fillMaxWidth()) {
                        Text(stringResource(R.string.action_back))
                    }
                }
            }
        }
    }
}
