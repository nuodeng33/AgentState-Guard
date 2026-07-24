package com.agentstate.guard.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

data class EnvItem(val name: String, val version: String, val status: String)

@Composable
fun EnvironmentScreen(items: List<EnvItem> = listOf(
    EnvItem("Claude Code", "2.1.217", "OK"),
    EnvItem("Node.js", "v22.23.1", "OK"),
    EnvItem("Python", "3.11.2", "OK"),
    EnvItem("Docker", "N/A", "UNREACHABLE"),
    EnvItem("Tailscale", "N/A", "NOT INSTALLED"),
)) {
    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        item { Text("Environment", style = MaterialTheme.typography.headlineSmall) }
        items(items) { item ->
            Card(modifier = Modifier.fillMaxWidth()) {
                Row(
                    modifier = Modifier.padding(16.dp).fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(item.name, style = MaterialTheme.typography.titleSmall)
                        Text(item.version, style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Text(item.status, color = when(item.status) {
                        "OK", "PASS" -> MaterialTheme.colorScheme.primary
                        "UNREACHABLE", "NOT INSTALLED" -> MaterialTheme.colorScheme.outline
                        else -> MaterialTheme.colorScheme.error
                    })
                }
            }
        }
    }
}
