package main

import (
	"fmt"
	"example.com/svc/util"
)

// Server serves.
type Server struct{ Name string }

func (s *Server) Start() error {
	fmt.Println(util.Greet(s.Name))
	return helper()
}

func main() { s := &Server{Name: "x"}; s.Start() }
