package main

import "testing"

func TestOwnershipVerification(t *testing.T) {
	alice, err := NewWallet()
	if err != nil {
		t.Fatalf("NewWallet(Alice): %v", err)
	}
	bob, err := NewWallet()
	if err != nil {
		t.Fatalf("NewWallet(Bob): %v", err)
	}
	if alice.Address == bob.Address {
		t.Fatal("two generated wallets unexpectedly have the same address")
	}
	if err := ValidateAddress(alice.Address); err != nil {
		t.Fatalf("Alice address validation failed: %v", err)
	}
	if err := ValidateAddress(bob.Address); err != nil {
		t.Fatalf("Bob address validation failed: %v", err)
	}

	transfer := TransferData{To: bob.Address, Amount: 50000000}
	if err := transfer.Validate(); err != nil {
		t.Fatalf("transfer validation failed: %v", err)
	}
	signature, err := Sign(alice.PrivateKey, transfer.Message())
	if err != nil {
		t.Fatalf("Sign: %v", err)
	}
	ok, err := Verify(alice.PublicKey, transfer.Message(), signature)
	if err != nil || !ok {
		t.Fatalf("valid signature rejected: ok=%v err=%v", ok, err)
	}
}

func TestTamperingAndWrongOwnerAreRejected(t *testing.T) {
	alice, err := NewWallet()
	if err != nil {
		t.Fatal(err)
	}
	bob, err := NewWallet()
	if err != nil {
		t.Fatal(err)
	}
	transfer := TransferData{To: bob.Address, Amount: 12345}
	signature, err := Sign(alice.PrivateKey, transfer.Message())
	if err != nil {
		t.Fatal(err)
	}

	tampered := TransferData{To: bob.Address, Amount: 12346}
	ok, err := Verify(alice.PublicKey, tampered.Message(), signature)
	if err != nil || ok {
		t.Fatalf("tampered amount was accepted: ok=%v err=%v", ok, err)
	}
	ok, err = Verify(bob.PublicKey, transfer.Message(), signature)
	if err != nil || ok {
		t.Fatalf("wrong public key was accepted: ok=%v err=%v", ok, err)
	}
}

func TestMalformedSignatureReturnsError(t *testing.T) {
	wallet, err := NewWallet()
	if err != nil {
		t.Fatal(err)
	}
	ok, err := Verify(wallet.PublicKey, []byte("message"), []byte("not-a-DER-signature"))
	if err == nil || ok {
		t.Fatalf("malformed signature did not return an error: ok=%v err=%v", ok, err)
	}
}

func TestAddressChecksumRejectsMutation(t *testing.T) {
	wallet, err := NewWallet()
	if err != nil {
		t.Fatal(err)
	}
	mutated := wallet.Address[:len(wallet.Address)-1] + "1"
	if mutated == wallet.Address {
		mutated = wallet.Address[:len(wallet.Address)-1] + "2"
	}
	if err := ValidateAddress(mutated); err == nil {
		t.Fatal("mutated address passed checksum validation")
	}
}
