package com.agentstate.guard

import android.content.Context
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import com.agentstate.guard.ui.AgentStateApp
import com.agentstate.guard.ui.LocaleController
import com.agentstate.guard.ui.theme.AgentStateTheme

class MainActivity : ComponentActivity() {
    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleController.wrap(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            AgentStateTheme {
                AgentStateApp()
            }
        }
    }
}
