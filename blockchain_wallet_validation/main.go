package main

import (
	"encoding/hex"
	"fmt"
	"log"
)

func main() {
	alice, err := NewWallet()
	if err != nil {
		log.Fatal(err)
	}
	bob, err := NewWallet()
	if err != nil {
		log.Fatal(err)
	}

	transfer := TransferData{To: bob.Address, Amount: 50000000}
	if err := transfer.Validate(); err != nil {
		log.Fatal(err)
	}
	signature, err := Sign(alice.PrivateKey, transfer.Message())
	if err != nil {
		log.Fatal(err)
	}
	ok, err := Verify(alice.PublicKey, transfer.Message(), signature)
	if err != nil {
		log.Fatal(err)
	}

	tamperedTransfer := transfer
	tamperedTransfer.Amount++
	tamperedOK, tamperedErr := Verify(alice.PublicKey, tamperedTransfer.Message(), signature)
	wrongKeyOK, wrongKeyErr := Verify(bob.PublicKey, transfer.Message(), signature)

	fmt.Println("=== BTC ownership verification demo ===")
	fmt.Printf("Alice address: %s\n", alice.Address)
	fmt.Printf("Bob address:   %s\n", bob.Address)
	fmt.Printf("Transfer:      To=%s Amount=%d satoshi\n", transfer.To, transfer.Amount)
	fmt.Printf("Message:       %s\n", transfer.Message())
	fmt.Printf("Signature DER: %s\n", hex.EncodeToString(signature))
	fmt.Printf("Address checks: Alice=%t Bob=%t\n", ValidateAddress(alice.Address) == nil, ValidateAddress(bob.Address) == nil)
	fmt.Printf("Correct signature: ok=%t err=%v\n", ok, err)
	fmt.Printf("Tampered amount:  ok=%t err=%v\n", tamperedOK, tamperedErr)
	fmt.Printf("Wrong public key: ok=%t err=%v\n", wrongKeyOK, wrongKeyErr)
}
