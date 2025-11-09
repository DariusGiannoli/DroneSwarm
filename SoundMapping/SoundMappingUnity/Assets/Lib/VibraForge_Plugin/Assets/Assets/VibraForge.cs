using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;

[Serializable]
public class VibraForge : MonoBehaviour
{
    private static TcpSender sender;
    // --- MODIFIED: Added "slave_id" to the command dictionary ---
    private static Dictionary<string, int> command;

    void Start()
    {
        sender = this.GetComponent<TcpSender>();
        // --- MODIFIED: Initialize the new "slave_id" key ---
        command = new Dictionary<string, int>()
        {
            { "slave_id", 0 }, // Default to slave 0
            { "addr", -1 },
            { "mode", 0 },
            { "duty", 0 },
            { "freq", 2 }
        };
    }

    public static string DictionaryToString(Dictionary<string, int> dictionary)
    {
        string dictionaryString = "{";
        foreach (KeyValuePair<string, int> keyValues in dictionary)
        {
            dictionaryString += "\"" + keyValues.Key + "\": " + keyValues.Value + ", ";
        }
        return dictionaryString.TrimEnd(',', ' ') + "}";
    }

    // --- MODIFIED: The SendCommand method now accepts a slaveId ---
    public static void SendCommand(int slaveId, int addr, int mode, int duty, int freq)
    {
        if (sender == null) return; // Safety check

        // Add the target slaveId to the command
        command["slave_id"] = slaveId;
        command["addr"] = addr;
        command["mode"] = mode;
        command["duty"] = duty;
        command["freq"] = freq;

        sender.SendData(DictionaryToString(command));

        // Note: your saveInfoToJSON method might also need to be updated
        // to handle the slaveId if you want to log it.
        saveInfoToJSON.addHapticRecord(addr, duty, freq);
    }

    void OnApplicationQuit()
    {
        Reset();
    }

    // --- MODIFIED: Reset now turns off all actuators on all known slaves ---
    public static void Reset()
    {
        Debug.Log("Resetting all haptic slaves...");
        // Define the number of slaves you have.
        int numberOfSlaves = 2;

        for (int slaveId = 0; slaveId < numberOfSlaves; slaveId++)
        {
            // Reset all possible addresses for each slave.
            // Your API supports addresses 0-127.
            for (int i = 0; i < 128; i++)
            {
                SendCommand(slaveId, i, 0, 0, 0);
                // Optional small delay to avoid flooding the connection.
                if (i % 20 == 0)
                {
                    System.Threading.Thread.Sleep(50);
                }
            }
        }
    }
}