package com.agentstate.guard.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

data class Change(val file: String, val type: String, val before: String?, val after: String?)

@Composable
fun ChangesScreen(items: List<Change> = emptyList()) {
    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        item { Text("Changes", style = MaterialTheme.typography.headlineSmall) }
        if (items.isEmpty()) {
            item {
                Card(modifier = Modifier.fillMaxWidth()) {
                    Text("No changes detected", modifier = Modifier.padding(16.dp))
                }
            }
        }
        items(items) { change ->
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(change.file, style = MaterialTheme.typography.titleSmall)
                    Text("Type: ${change.type}", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (change.before != null && change.after != null) {
                        Text("Before: ${change.before}", style = MaterialTheme.typography.bodySmall)
                        Text("After: ${change.after}", style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.primary)
                    }
                }
            }
        }
    }
}
