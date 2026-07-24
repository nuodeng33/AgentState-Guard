package com.agentstate.guard.ui

import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.agentstate.guard.ui.screens.*

enum class Screen(val route: String, val label: String) {
    Home("home", "Home"),
    Environment("environment", "Environment"),
    Checkpoints("checkpoints", "Checkpoints"),
    Changes("changes", "Changes"),
    AI("ai", "AI"),
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AgentStateApp() {
    val navController = rememberNavController()
    val navBackStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = navBackStackEntry?.destination?.route

    Scaffold(
        bottomBar = {
            NavigationBar {
                Screen.entries.forEach { screen ->
                    NavigationBarItem(
                        icon = {
                            when (screen) {
                                Screen.Home -> Icon(Icons.Default.Home, null)
                                Screen.Environment -> Icon(Icons.Default.Build, null)
                                Screen.Checkpoints -> Icon(Icons.Default.History, null)
                                Screen.Changes -> Icon(Icons.Default.Difference, null)
                                Screen.AI -> Icon(Icons.Default.AutoAwesome, null)
                            }
                        },
                        label = { Text(screen.label) },
                        selected = currentRoute == screen.route,
                        onClick = {
                            if (currentRoute != screen.route) {
                                navController.navigate(screen.route) {
                                    popUpTo(Screen.Home.route) { saveState = true }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                            }
                        }
                    )
                }
            }
        }
    ) { padding ->
        NavHost(
            navController = navController,
            startDestination = Screen.Home.route,
            modifier = Modifier.padding(padding)
        ) {
            composable(Screen.Home.route) { HomeScreen() }
            composable(Screen.Environment.route) { EnvironmentScreen() }
            composable(Screen.Checkpoints.route) { CheckpointsScreen() }
            composable(Screen.Changes.route) { ChangesScreen() }
            composable(Screen.AI.route) { AIScreen() }
        }
    }
}
