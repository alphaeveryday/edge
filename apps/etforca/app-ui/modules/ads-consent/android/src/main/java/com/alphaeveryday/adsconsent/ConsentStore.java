package com.alphaeveryday.adsconsent;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.FileInputStream;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;

// No SDK initialization; errors must not be interpreted as an absent preference.
public final class ConsentStore {
  private final File directory;
  private static final String NAME = ".posthog-rn.json";

  public ConsentStore(File directory) { this.directory = directory; }
  public String read() throws IOException { return readFile(NAME); }

  private String readFile(String name) throws IOException {
    File[] entries = directory.listFiles();
    if (entries == null) throw new IOException("Cannot enumerate analytics preference directory");
    boolean found = false;
    for (File entry : entries) if (entry.getName().equals(name)) found = true;
    if (!found) return null;
    try (FileInputStream input = new FileInputStream(new File(directory, name));
         ByteArrayOutputStream output = new ByteArrayOutputStream()) {
      byte[] buffer = new byte[4096];
      int size;
      while ((size = input.read(buffer)) != -1) output.write(buffer, 0, size);
      return new String(output.toByteArray(), StandardCharsets.UTF_8);
    }
  }

  public void write(String value) throws IOException {
    File temp = new File(directory, NAME + ".tmp");
    try (FileOutputStream output = new FileOutputStream(temp)) {
      output.write(value.getBytes(StandardCharsets.UTF_8));
      output.getFD().sync();
    }
    if (!temp.renameTo(new File(directory, NAME))) throw new IOException("Cannot replace analytics preference");
  }
}
