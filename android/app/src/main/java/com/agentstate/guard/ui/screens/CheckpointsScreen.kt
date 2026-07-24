package com.agentstate.guard.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

data class Checkpoint(val id: Int, val label: String, val time: String, val files: Int, val restorable: Int)

@Composable
fun CheckpointsScreen(items: List<Checkpoint> = emptyList()) {
    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        item { Text("Checkpoints", style = MaterialTheme.typography.headlineSmall) }
        if (items.isEmpty()) {
            item {
                Card(modifier = Modifier.fillMaxWidth()) {
                    Text("No checkpoints", modifier = Modifier.padding(16.dp))
                }
            }
        }
        items(items) { cp ->
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text("#${cp.id} ${cp.label}", style = MaterialTheme.typography.titleSmall)
                    Text(cp.time, style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                        Text("${cp.files} files", style = MaterialTheme.typography.bodySmall)
                        Text("${cp.restorable} restorable", style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.primary)
                    }
                }
            }
        }
    }
}
