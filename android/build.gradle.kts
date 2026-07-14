buildscript {
    repositories {
        google()
        mavenCentral()
    }
    dependencies {
        // Chaquopy embeds a CPython interpreter + pip packages into the APK.
        // Pin a version known to work with the AGP/Kotlin versions below;
        // check chaquo.com/chaquopy/doc/current/versions.html when upgrading.
        classpath("com.chaquo.python:gradle:17.0.0")
    }
}

plugins {
    id("com.android.application") version "9.2.1" apply false
    id("org.jetbrains.kotlin.android") version "2.2.10" apply false
}
