import com.alphaeveryday.adsconsent.ConsentStore;
import java.io.File;
import java.nio.file.Files;

public class TestConsentStore {
  static void check(boolean ok) { if (!ok) throw new AssertionError(); }
  public static void main(String[] args) throws Exception {
    File directory = Files.createTempDirectory("consent-store-test").toFile();
    ConsentStore store = new ConsentStore(directory);
    check(store.read() == null);
    String refusal = "{\"version\":\"v1\",\"content\":{\"opted_out\":true}}";
    store.write(refusal);
    check(refusal.equals(new ConsentStore(directory).read()));
    File file = new File(directory, ".posthog-rn.json");
    try { new ConsentStore(file).read(); throw new AssertionError("list failure hidden"); }
    catch (java.io.IOException expected) {}
    check(file.delete()); check(file.mkdir());
    try { store.read(); throw new AssertionError("read failure hidden"); }
    catch (java.io.IOException expected) {}
    try { store.write(refusal); throw new AssertionError("replace failure hidden"); }
    catch (java.io.IOException expected) {}
    System.out.println("Java consent file: 5 scenarios passed");
  }
}
